import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime

import nuke


DEFAULT_WIDTH = 1920
DEFAULT_CRF = 18
DEFAULT_PRESET = "medium"
DEFAULT_SUFFIX = "lores_nraz"
DEFAULT_FFMPEG = r"C:\ffmpeg-2024-08-21-git-9d15fe77e3-full_build\bin\ffmpeg.exe"


def _has_knob(node, name):
    return name in node.knobs()


def _find_ffmpeg(node=None):
    if node is not None and _has_knob(node, "auto_lores_ffmpeg"):
        value = node["auto_lores_ffmpeg"].value().strip()
        if value and os.path.isfile(value):
            return value

    if os.path.isfile(DEFAULT_FFMPEG):
        return DEFAULT_FFMPEG

    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        return ffmpeg

    return None


def _find_ffprobe(node=None):
    ffmpeg = _find_ffmpeg(node)
    if ffmpeg:
        candidate = os.path.join(os.path.dirname(ffmpeg), "ffprobe.exe")
        if os.path.isfile(candidate):
            return candidate
        candidate = os.path.join(os.path.dirname(ffmpeg), "ffprobe")
        if os.path.isfile(candidate):
            return candidate

    ffprobe = shutil.which("ffprobe")
    if ffprobe:
        return ffprobe

    return None


def _read_knob(node, name, default):
    if _has_knob(node, name):
        return node[name].value()
    return default


def _set_status(node, status, output_path=None):
    if _has_knob(node, "auto_lores_status"):
        node["auto_lores_status"].setValue(status)
    if output_path and _has_knob(node, "auto_lores_last_output"):
        node["auto_lores_last_output"].setValue(output_path)


def add_knobs(node=None):
    node = node or nuke.thisNode()
    if node is None or node.Class() != "Write":
        return

    if not _has_knob(node, "auto_lores_tab"):
        node.addKnob(nuke.Tab_Knob("auto_lores_tab", "Auto Lores"))

    if not _has_knob(node, "auto_lores_enable"):
        enable = nuke.Boolean_Knob("auto_lores_enable", "Create lores after render")
        enable.setValue(False)
        node.addKnob(enable)

    if not _has_knob(node, "auto_lores_width"):
        width = nuke.Int_Knob("auto_lores_width", "Proxy width")
        width.setValue(DEFAULT_WIDTH)
        node.addKnob(width)

    if not _has_knob(node, "auto_lores_crf"):
        crf = nuke.Int_Knob("auto_lores_crf", "CRF")
        crf.setValue(DEFAULT_CRF)
        node.addKnob(crf)

    if not _has_knob(node, "auto_lores_preset"):
        preset = nuke.Enumeration_Knob(
            "auto_lores_preset",
            "Preset",
            ["slow", "medium", "fast", "faster", "veryfast"],
        )
        preset.setValue(DEFAULT_PRESET)
        node.addKnob(preset)

    if not _has_knob(node, "auto_lores_existing_version"):
        existing = nuke.Enumeration_Knob(
            "auto_lores_existing_version",
            "If versioned lores exists",
            ["skip", "overwrite"],
        )
        existing.setValue("skip")
        node.addKnob(existing)

    if not _has_knob(node, "auto_lores_suffix"):
        suffix = nuke.String_Knob("auto_lores_suffix", "Output suffix")
        suffix.setValue(DEFAULT_SUFFIX)
        node.addKnob(suffix)

    if not _has_knob(node, "auto_lores_ffmpeg"):
        ffmpeg = nuke.File_Knob("auto_lores_ffmpeg", "ffmpeg")
        ffmpeg.setValue(_find_ffmpeg() or "")
        node.addKnob(ffmpeg)

    if not _has_knob(node, "auto_lores_status"):
        status = nuke.String_Knob("auto_lores_status", "Status")
        status.setValue("idle")
        status.setEnabled(False)
        node.addKnob(status)

    if not _has_knob(node, "auto_lores_last_output"):
        last_output = nuke.File_Knob("auto_lores_last_output", "Last lores")
        last_output.setEnabled(False)
        node.addKnob(last_output)

    if not _has_knob(node, "auto_lores_preview_path"):
        preview_path = nuke.PyScript_Knob("auto_lores_preview_path", "Preview output path")
        preview_path.setCommand("import auto_lores_proxy; auto_lores_proxy.preview_for_this_node()")
        node.addKnob(preview_path)

    if not _has_knob(node, "auto_lores_reveal_file"):
        reveal_file = nuke.PyScript_Knob("auto_lores_reveal_file", "Reveal lores file")
        reveal_file.setCommand("import auto_lores_proxy; auto_lores_proxy.reveal_last_output(file_select=True)")
        node.addKnob(reveal_file)

    if not _has_knob(node, "auto_lores_reveal_folder"):
        reveal_folder = nuke.PyScript_Knob("auto_lores_reveal_folder", "Open lores folder")
        reveal_folder.setCommand("import auto_lores_proxy; auto_lores_proxy.reveal_last_output(file_select=False)")
        node.addKnob(reveal_folder)

    if not _has_knob(node, "auto_lores_copy_settings"):
        copy_settings = nuke.PyScript_Knob("auto_lores_copy_settings", "Copy settings to selected Writes")
        copy_settings.setCommand("import auto_lores_proxy; auto_lores_proxy.copy_settings_from_this_node()")
        node.addKnob(copy_settings)

    if not _has_knob(node, "auto_lores_render_now"):
        render_now = nuke.PyScript_Knob("auto_lores_render_now", "Create lores now")
        render_now.setCommand("import auto_lores_proxy; auto_lores_proxy.create_for_this_node()")
        node.addKnob(render_now)


def add_knobs_to_all_writes():
    for node in nuke.allNodes("Write"):
        add_knobs(node)


def _normalise_path(path):
    return os.path.normpath(path.replace("/", os.sep))


def _is_movie_path(path):
    return os.path.splitext(path)[1].lower() in [".mov", ".mp4"]


def _has_lut_marker(path):
    parts = re.split(r"[\\/]+", path)
    return any(part.upper() == "LUT" or "LUT" in part.upper() for part in parts)


def _find_version_token(name):
    version = None
    for token in re.split(r"[_\s.-]+", name):
        match = re.fullmatch(r"v(\d{3})", token, flags=re.IGNORECASE)
        if match:
            version = match.group(1)
    return version


def _replace_hires_with_lores(path):
    parts = _normalise_path(path).split(os.sep)
    lower_parts = [part.lower() for part in parts]
    if "hires" not in lower_parts:
        return None

    index = lower_parts.index("hires")
    root = os.sep.join(parts[:index])
    return os.path.join(root, "lores")


def _next_version(output_dir, base_name, suffix):
    pattern = re.compile(
        r"^%s_%s_v(\d{3})\.mp4$"
        % (re.escape(base_name), re.escape(suffix)),
        flags=re.IGNORECASE,
    )
    max_version = 0
    if os.path.isdir(output_dir):
        for filename in os.listdir(output_dir):
            match = pattern.match(filename)
            if match:
                max_version = max(max_version, int(match.group(1)))
    return "%03d" % (max_version + 1)


def build_output_path(input_path, suffix=DEFAULT_SUFFIX):
    input_path = _normalise_path(input_path)
    output_dir = _replace_hires_with_lores(input_path)
    if output_dir is None:
        raise ValueError("Input path must be inside a folder named 'hires': %s" % input_path)

    base_name = os.path.splitext(os.path.basename(input_path))[0]
    version = _find_version_token(base_name)

    if _has_lut_marker(input_path):
        output_dir = os.path.join(output_dir, "LUT")

    if version:
        output_name = "%s_%s.mp4" % (base_name, suffix)
        return os.path.join(output_dir, output_name), True

    version = _next_version(output_dir, base_name, suffix)
    output_name = "%s_%s_v%s.mp4" % (base_name, suffix, version)
    return os.path.join(output_dir, output_name), False


def _build_ffmpeg_command(node, input_path, output_path):
    ffmpeg = _find_ffmpeg(node)
    if not ffmpeg:
        raise RuntimeError("ffmpeg was not found.")

    width = int(_read_knob(node, "auto_lores_width", DEFAULT_WIDTH))
    crf = int(_read_knob(node, "auto_lores_crf", DEFAULT_CRF))
    preset = str(_read_knob(node, "auto_lores_preset", DEFAULT_PRESET))

    vf = "scale=%d:trunc((%d/(iw*sar/ih))/2)*2,setsar=1" % (width, width)

    return [
        ffmpeg,
        "-y",
        "-i",
        input_path,
        "-map",
        "0:v:0",
        "-vf",
        vf,
        "-c:v",
        "libx264",
        "-profile:v",
        "high",
        "-level:v",
        "4.1",
        "-pix_fmt",
        "yuv420p",
        "-crf",
        str(crf),
        "-preset",
        preset,
        "-movflags",
        "+faststart",
        "-an",
        "-sn",
        "-dn",
        output_path,
    ]


def _write_log(output_path, command, result):
    log_dir = os.path.dirname(output_path)
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, "_auto_lores_proxy.log")
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    with open(log_path, "a", encoding="utf-8", errors="replace") as handle:
        handle.write("\n[%s]\n" % stamp)
        handle.write("COMMAND: %s\n" % subprocess.list2cmdline(command))
        handle.write("EXIT: %s\n" % result.returncode)
        if result.stdout:
            handle.write("STDOUT:\n%s\n" % result.stdout)
        if result.stderr:
            handle.write("STDERR:\n%s\n" % result.stderr)
    return log_path


def _probe_movie(node, input_path):
    ffprobe = _find_ffprobe(node)
    if not ffprobe:
        return True

    command = [
        ffprobe,
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=codec_name,width,height",
        "-of",
        "default=nw=1",
        input_path,
    ]
    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return result.returncode == 0 and "codec_name=" in result.stdout


def _wait_for_readable_movie(node, input_path, timeout_seconds=300, interval_seconds=2):
    deadline = time.time() + timeout_seconds
    last_size = -1
    stable_checks = 0
    required_stable_checks = 5

    while time.time() < deadline:
        if not os.path.exists(input_path):
            time.sleep(interval_seconds)
            continue

        size = os.path.getsize(input_path)
        if size > 0 and size == last_size:
            stable_checks += 1
        else:
            stable_checks = 0
            last_size = size

        if stable_checks >= required_stable_checks and _probe_movie(node, input_path):
            return True

        time.sleep(interval_seconds)

    raise RuntimeError("Rendered movie is not readable yet: %s" % input_path)


def _run_ffmpeg(node, input_path, output_path):
    command = _build_ffmpeg_command(node, input_path, output_path)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    startupinfo = None
    if os.name == "nt":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        startupinfo=startupinfo,
    )
    log_path = _write_log(output_path, command, result)

    if result.returncode != 0 or not os.path.exists(output_path):
        raise RuntimeError("Auto Lores failed. Log: %s" % log_path)

    return output_path


def preview_for_node(node):
    add_knobs(node)
    input_path = _normalise_path(node["file"].evaluate())
    suffix = str(_read_knob(node, "auto_lores_suffix", DEFAULT_SUFFIX)).strip() or DEFAULT_SUFFIX
    output_path, source_has_version = build_output_path(input_path, suffix=suffix)

    if os.path.exists(output_path) and not source_has_version:
        output_path, source_has_version = build_output_path(input_path, suffix=suffix)

    _set_status(node, "preview ready", output_path)
    nuke.message("Auto Lores output path:\n\n%s" % output_path)
    return output_path


def preview_for_this_node():
    node = nuke.thisNode()
    if node is None or node.Class() != "Write":
        nuke.message("Open this from a Write node.")
        return None
    try:
        return preview_for_node(node)
    except Exception as exc:
        _set_status(node, "preview failed")
        nuke.message(str(exc))
        return None


def create_for_this_node():
    node = nuke.thisNode()
    if node is None or node.Class() != "Write":
        nuke.message("Open this from a Write node.")
        return None
    try:
        return create_for_node(node, manual=True)
    except Exception as exc:
        _set_status(node, "failed")
        nuke.message(str(exc))
        return None


def reveal_last_output(file_select=True):
    node = nuke.thisNode()
    if node is None or node.Class() != "Write":
        nuke.message("Open this from a Write node.")
        return

    output_path = ""
    if _has_knob(node, "auto_lores_last_output"):
        output_path = node["auto_lores_last_output"].value()
    output_path = _normalise_path(output_path) if output_path else ""

    if not output_path:
        try:
            output_path = preview_for_node(node)
        except Exception as exc:
            nuke.message(str(exc))
            return

    folder = output_path if os.path.isdir(output_path) else os.path.dirname(output_path)
    if not folder:
        nuke.message("No lores folder is known yet.")
        return

    if os.name == "nt":
        if file_select and os.path.exists(output_path):
            subprocess.Popen(["explorer", "/select,", output_path])
        else:
            os.startfile(folder)
        return

    opener = "open" if sys.platform == "darwin" else "xdg-open"
    subprocess.Popen([opener, folder])


def copy_settings(source, target):
    add_knobs(source)
    add_knobs(target)
    knob_names = [
        "auto_lores_enable",
        "auto_lores_width",
        "auto_lores_crf",
        "auto_lores_preset",
        "auto_lores_existing_version",
        "auto_lores_suffix",
        "auto_lores_ffmpeg",
    ]
    for knob_name in knob_names:
        if _has_knob(source, knob_name) and _has_knob(target, knob_name):
            target[knob_name].setValue(source[knob_name].value())


def copy_settings_from_this_node():
    source = nuke.thisNode()
    if source is None or source.Class() != "Write":
        nuke.message("Open this from a Write node.")
        return

    targets = [node for node in nuke.selectedNodes() if node.Class() == "Write" and node is not source]
    if not targets:
        nuke.message("Select other Write nodes to copy settings to.")
        return

    for target in targets:
        copy_settings(source, target)
    nuke.message("Copied Auto Lores settings to %d Write node(s)." % len(targets))


def copy_settings_from_first_selected():
    nodes = [node for node in nuke.selectedNodes() if node.Class() == "Write"]
    if len(nodes) < 2:
        nuke.message("Select at least two Write nodes. The first selected node is the source.")
        return

    source = nodes[0]
    for target in nodes[1:]:
        copy_settings(source, target)
    nuke.message("Copied Auto Lores settings from %s to %d Write node(s)." % (source.name(), len(nodes) - 1))


def create_for_node(node, input_path=None, manual=False):
    add_knobs(node)

    if not manual and not bool(_read_knob(node, "auto_lores_enable", False)):
        return None

    _set_status(node, "checking")
    input_path = input_path or node["file"].evaluate()
    input_path = _normalise_path(input_path)

    if not _is_movie_path(input_path):
        _set_status(node, "failed")
        raise ValueError("Auto Lores supports movie Write paths only: %s" % input_path)

    if not os.path.exists(input_path):
        _set_status(node, "failed")
        raise ValueError("Rendered movie was not found: %s" % input_path)

    _set_status(node, "waiting for movie")
    _wait_for_readable_movie(node, input_path)

    suffix = str(_read_knob(node, "auto_lores_suffix", DEFAULT_SUFFIX)).strip() or DEFAULT_SUFFIX
    output_path, source_has_version = build_output_path(input_path, suffix=suffix)
    _set_status(node, "ready", output_path)

    if os.path.exists(output_path):
        if source_has_version:
            policy = str(_read_knob(node, "auto_lores_existing_version", "skip"))
            if policy == "skip":
                _set_status(node, "skipped existing", output_path)
                nuke.tprint("[Auto Lores] Skipping existing file: %s" % output_path)
                return output_path
        else:
            output_path, source_has_version = build_output_path(input_path, suffix=suffix)
            _set_status(node, "ready", output_path)

    nuke.tprint("[Auto Lores] Creating: %s" % output_path)
    _set_status(node, "encoding", output_path)
    try:
        result = _run_ffmpeg(node, input_path, output_path)
    except Exception:
        _set_status(node, "failed", output_path)
        raise
    _set_status(node, "done", result)
    nuke.tprint("[Auto Lores] Done: %s" % result)
    return result


def create_for_selected():
    nodes = [node for node in nuke.selectedNodes() if node.Class() == "Write"]
    if not nodes:
        nuke.message("Select at least one Write node.")
        return

    failures = []
    for node in nodes:
        try:
            create_for_node(node, manual=True)
        except Exception as exc:
            failures.append("%s: %s" % (node.name(), exc))

    if failures:
        nuke.message("Auto Lores failed:\n\n%s" % "\n".join(failures))


def enable_for_selected():
    nodes = [node for node in nuke.selectedNodes() if node.Class() == "Write"]
    if not nodes:
        nuke.message("Select at least one Write node.")
        return

    for node in nodes:
        add_knobs(node)
        node["auto_lores_enable"].setValue(True)


def after_render_callback():
    node = nuke.thisNode()
    if node is None or node.Class() != "Write":
        return

    try:
        create_for_node(node)
    except Exception as exc:
        nuke.tprint("[Auto Lores] %s" % exc)
        if nuke.env.get("gui"):
            nuke.message(str(exc))


def install():
    nuke.addOnUserCreate(lambda: add_knobs(nuke.thisNode()), nodeClass="Write")
    nuke.addOnScriptLoad(add_knobs_to_all_writes)
    nuke.addAfterRender(after_render_callback, nodeClass="Write")

    menu = nuke.menu("Nuke").addMenu("Auto Lores")
    menu.addCommand("Enable on selected Write", "import auto_lores_proxy; auto_lores_proxy.enable_for_selected()")
    menu.addCommand("Create for selected Write now", "import auto_lores_proxy; auto_lores_proxy.create_for_selected()")
    menu.addCommand("Copy settings from first selected Write", "import auto_lores_proxy; auto_lores_proxy.copy_settings_from_first_selected()")
    menu.addCommand("Add knobs to existing Writes", "import auto_lores_proxy; auto_lores_proxy.add_knobs_to_all_writes()")

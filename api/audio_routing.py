import subprocess
import time
from typing import Dict, List, Tuple, Optional


def _run_pactl_command(args: List[str]) -> str:
    """Runs a pactl command and returns stdout as text. Raises on non-zero exit code."""
    completed = subprocess.run(["pactl", *args], check=True, capture_output=True, text=True)
    return completed.stdout


def create_virtual_audio_pair(prefix: str) -> Tuple[str, str, int, int]:
    """
    Creates a dedicated PulseAudio null-sink and its monitor-based virtual source.

    Returns:
        sink_name, source_name, sink_module_id, source_module_id
    """
    sink_name = f"{prefix}_sink"
    source_name = f"{prefix}_mic"

    sink_module_str = _run_pactl_command([
        "load-module", "module-null-sink",
        f"sink_name={sink_name}", f"sink_properties=device.description={sink_name}"
    ]).strip()
    sink_module_id = int(sink_module_str)

    source_module_str = _run_pactl_command([
        "load-module", "module-virtual-source",
        f"source_name={source_name}", f"master={sink_name}.monitor"
    ]).strip()
    source_module_id = int(source_module_str)

    return sink_name, source_name, sink_module_id, source_module_id


def unload_module(module_id: int) -> None:
    """Unloads a PulseAudio module by id. Ignores errors if already gone."""
    try:
        _run_pactl_command(["unload-module", str(module_id)])
    except subprocess.SubprocessError:
        pass


def list_sink_inputs() -> List[Dict]:
    """
    Parses `pactl list sink-inputs` output into a list of dicts with keys:
    { index: int, properties: {k: v}, raw: [lines] }
    """
    out = _run_pactl_command(["list", "sink-inputs"])
    entries: List[Dict] = []
    cur: Optional[Dict] = None
    properties_section = False

    for line in out.splitlines():
        line = line.rstrip()
        if line.startswith("Sink Input #"):
            if cur:
                entries.append(cur)
            cur = {"index": int(line.split("#")[1]), "properties": {}, "raw": []}
            properties_section = False
        elif cur is not None:
            cur["raw"].append(line)
            if line.strip() == "Properties:":
                properties_section = True
                continue
            if properties_section:
                # property lines look like: \tapplication.name = "Chromium"
                parts = line.strip().split("=", 1)
                if len(parts) == 2:
                    key = parts[0].strip()
                    val = parts[1].strip().strip('"')
                    cur["properties"][key] = val

    if cur:
        entries.append(cur)
    return entries


def list_source_outputs() -> List[Dict]:
    """
    Parses `pactl list source-outputs` output into a list of dicts with keys:
    { index: int, properties: {k: v}, raw: [lines] }
    """
    out = _run_pactl_command(["list", "source-outputs"])
    entries: List[Dict] = []
    cur: Optional[Dict] = None
    properties_section = False

    for line in out.splitlines():
        line = line.rstrip()
        if line.startswith("Source Output #"):
            if cur:
                entries.append(cur)
            cur = {"index": int(line.split("#")[1]), "properties": {}, "raw": []}
            properties_section = False
        elif cur is not None:
            cur["raw"].append(line)
            if line.strip() == "Properties:":
                properties_section = True
                continue
            if properties_section:
                parts = line.strip().split("=", 1)
                if len(parts) == 2:
                    key = parts[0].strip()
                    val = parts[1].strip().strip('"')
                    cur["properties"][key] = val

    if cur:
        entries.append(cur)
    return entries


def move_sink_input(index: int, sink_name: str) -> None:
    _run_pactl_command(["move-sink-input", str(index), sink_name])


def move_source_output(index: int, source_name: str) -> None:
    _run_pactl_command(["move-source-output", str(index), source_name])


def filter_chrome_entries(entries: List[Dict]) -> List[Dict]:
    """Keep entries whose application is Chrome/Chromium."""
    result = []
    for e in entries:
        props = e.get("properties", {})
        app_name = props.get("application.name", "").lower()
        app_proc = props.get("application.process.binary", "").lower()
        if "chrome" in app_name or "chromium" in app_name or "chrome" in app_proc or "chromium" in app_proc:
            result.append(e)
    return result


def diff_indices(before: List[Dict], after: List[Dict]) -> List[int]:
    """Returns indices present in `after` but not in `before`."""
    before_idx = {e["index"] for e in before}
    return [e["index"] for e in after if e["index"] not in before_idx]


def wait_and_route_new_streams(
    target_meet_sink: str,
    target_bot_mic: str,
    timeout_sec: float = 20.0,
    poll_interval_sec: float = 0.5,
) -> Tuple[List[int], List[int]]:
    """
    Waits for new Chrome sink-inputs and source-outputs to appear, then moves them
    to the provided sink/source. Returns two lists of moved indices.
    """
    t0 = time.time()
    moved_sink_inputs: List[int] = []
    moved_source_outputs: List[int] = []

    base_sinks = filter_chrome_entries(list_sink_inputs())
    base_sources = filter_chrome_entries(list_source_outputs())

    while time.time() - t0 < timeout_sec:
        cur_sinks = filter_chrome_entries(list_sink_inputs())
        cur_sources = filter_chrome_entries(list_source_outputs())

        new_sink_indices = diff_indices(base_sinks, cur_sinks)
        new_source_indices = diff_indices(base_sources, cur_sources)

        for idx in new_sink_indices:
            try:
                move_sink_input(idx, target_meet_sink)
                moved_sink_inputs.append(idx)
            except subprocess.SubprocessError:
                pass

        for idx in new_source_indices:
            try:
                move_source_output(idx, target_bot_mic)
                moved_source_outputs.append(idx)
            except subprocess.SubprocessError:
                pass

        if new_sink_indices or new_source_indices:
            # Update baselines to avoid re-moving same indices
            base_sinks = cur_sinks
            base_sources = cur_sources

        # If both sides have at least one move, we can return early
        if moved_sink_inputs and moved_source_outputs:
            return moved_sink_inputs, moved_source_outputs

        time.sleep(poll_interval_sec)

    return moved_sink_inputs, moved_source_outputs


def ensure_routing(target_meet_sink: str, target_bot_mic: str) -> Tuple[int, int]:
    """
    Idempotently moves ALL Chrome sink-inputs to target_meet_sink and
    ALL Chrome source-outputs to target_bot_mic. Returns (moved_sinks_count, moved_sources_count).
    Safe to call repeatedly; useful for background enforcement.
    """
    sinks = filter_chrome_entries(list_sink_inputs())
    sources = filter_chrome_entries(list_source_outputs())

    moved_sinks = 0
    moved_sources = 0

    for e in sinks:
        idx = e.get("index")
        if idx is None:
            continue
        try:
            move_sink_input(idx, target_meet_sink)
            moved_sinks += 1
        except subprocess.SubprocessError:
            pass

    for e in sources:
        idx = e.get("index")
        if idx is None:
            continue
        try:
            move_source_output(idx, target_bot_mic)
            moved_sources += 1
        except subprocess.SubprocessError:
            pass

    return moved_sinks, moved_sources

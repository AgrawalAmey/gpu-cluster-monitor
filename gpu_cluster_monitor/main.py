import subprocess
import time
import threading
from rich.live import Live
from rich.table import Table
from rich.console import Console, Group
from rich.text import Text
from rich.style import Style
from rich.panel import Panel
import rich.box # Added for table box styles
import argparse
import os
import yaml
import sys

# --- Default Configuration ---
DEFAULT_CLUSTER_CONFIG_DIR = os.path.expanduser("~/.gpu-cluster-monitor")

# --- Thresholds for color coding ---
TEMP_WARN_THRESHOLD = 75
TEMP_CRIT_THRESHOLD = 85

# --- Timeouts ---
SSH_TIMEOUT = 10
NVIDIA_SMI_TIMEOUT = 10

# --- Emojis ---
STATUS_EMOJI_OK = "✅"
STATUS_EMOJI_WARN = "⚠️"
STATUS_EMOJI_ERROR = "❌"
STATUS_EMOJI_UNREACHABLE = "❓"  # Used for host error, init, or genuinely unknown
BUSY_GPU_EMOJI = "🔥"
IDLE_GPU_EMOJI = "🧊"
# STATUS_EMOJI_UPDATING = "⏳" # Can be added if we want to show this specific status

# --- Rich Styles (remains hardcoded for now) ---
STYLE_OK = Style(color="green")
STYLE_CRITICAL = Style(color="red", bold=True)
STYLE_WARNING = Style(color="yellow")
STYLE_ERROR = Style(color="bright_red", bold=True)
STYLE_HOST = Style(color="cyan", bold=True)
STYLE_GPU_NAME = Style(color="magenta")
STYLE_DIM = Style(dim=True)

CONSOLE = Console()  # Restored CONSOLE global instance

# --- Default Application Settings ---
DEFAULT_SETTINGS = {
    "refresh_interval": 5,
    "temp_warn_threshold": TEMP_WARN_THRESHOLD,
    "temp_crit_threshold": TEMP_CRIT_THRESHOLD,
    "ssh_timeout": SSH_TIMEOUT,
    "nvidia_smi_timeout": NVIDIA_SMI_TIMEOUT,
    "status_emoji_ok": STATUS_EMOJI_OK,
    "status_emoji_warn": STATUS_EMOJI_WARN,
    "status_emoji_error": STATUS_EMOJI_ERROR,
    "status_emoji_unreachable": STATUS_EMOJI_UNREACHABLE,
    "status_emoji_updating": "⏳",  # Default for updating status
    "busy_gpu_emoji": BUSY_GPU_EMOJI,
    "idle_gpu_emoji": IDLE_GPU_EMOJI,
}

GLOBAL_SETTINGS_FILENAME = "settings.yaml"


# --- Utility Functions ---
def load_global_settings(config_dir: str) -> dict:
    """Loads global application settings from a YAML file in the config directory."""
    settings_path = os.path.join(config_dir, GLOBAL_SETTINGS_FILENAME)
    settings = DEFAULT_SETTINGS.copy()  # Start with defaults

    if os.path.exists(settings_path):
        try:
            with open(settings_path, "r") as f:
                user_settings = yaml.safe_load(f)
            if user_settings:
                settings.update(user_settings)  # Override defaults with user settings
        except yaml.YAMLError as e:
            CONSOLE.print(
                f"[bold red]Error parsing global settings file ({settings_path}): {e}[/bold red]"
            )
            CONSOLE.print("[yellow]Using default settings.[/yellow]")
        except OSError as e:
            CONSOLE.print(
                f"[bold red]Error reading global settings file ({settings_path}): {e}[/bold red]"
            )
            CONSOLE.print("[yellow]Using default settings.[/yellow]")
    # else: # Optional: Inform user that default settings are used if file doesn't exist
    # CONSOLE.print(f"[dim]Global settings file not found at {settings_path}. Using default settings.[/dim]")
    # CONSOLE.print(f"[dim]You can create it to customize behavior.[/dim]")
    return settings


def _natural_sort_key_for_host(host_name: str) -> tuple:
    """Helper for natural sorting of hostnames like h1, h2, h10."""
    parts = []
    current_part = ""
    for char_val in host_name:
        char_is_digit = char_val.isdigit()
        prev_char_is_digit = current_part[-1:].isdigit() if current_part else None

        if current_part and (char_is_digit != prev_char_is_digit):
            parts.append(int(current_part) if prev_char_is_digit else current_part)
            current_part = char_val
        else:
            current_part += char_val
    if current_part:
        parts.append(int(current_part) if current_part.isdigit() else current_part)
    return tuple(parts)


def _natural_sort_key_for_gpu(gpu_data: dict) -> tuple:
    """Helper for sorting GPU data, primarily by host (naturally), then by GPU ID."""
    host_sort_key = _natural_sort_key_for_host(gpu_data.get("host", ""))
    gpu_id_val = gpu_data.get("gpu_id")
    # Ensure consistent sorting for items with/without GPU ID
    return (host_sort_key, gpu_id_val if isinstance(gpu_id_val, int) else -1)


def _format_gpu_ids_to_ranges(gpu_ids: list[int]) -> str:
    """Formats a list of GPU IDs into a string with ranges, e.g., '0-2, 4, 6-7'."""
    if not gpu_ids:
        return Text("None", style="dim")  # Or an empty string if preferred

    # Ensure IDs are integers and sorted
    ids = sorted([int(gid) for gid in gpu_ids])

    ranges = []
    if not ids:
        return Text("None", style="dim")

    start_range = ids[0]
    for i in range(1, len(ids)):
        if ids[i] != ids[i - 1] + 1:
            # End of a range or a single number
            if start_range == ids[i - 1]:
                ranges.append(str(start_range))
            else:
                ranges.append(f"{start_range}-{ids[i - 1]}")
            start_range = ids[i]

    # Add the last range or single number
    if start_range == ids[-1]:
        ranges.append(str(start_range))
    else:
        ranges.append(f"{start_range}-{ids[-1]}")

    return ", ".join(ranges)


def load_cluster_config(config_path):
    try:
        with open(config_path, "r") as f:
            config = yaml.safe_load(f)
        return config
    except FileNotFoundError:
        CONSOLE.print(
            f"[bold red]Error: Config file not found at {config_path}[/bold red]"
        )
        return None
    except yaml.YAMLError as e:
        CONSOLE.print(
            f"[bold red]Error parsing YAML config file {config_path}: {e}[/bold red]"
        )
        return None
    except Exception as e:
        CONSOLE.print(
            f"[bold red]An unexpected error occurred while loading config {config_path}: {e}[/bold red]"
        )
        return None


def get_gpu_info_subprocess(
    hostname,
    cli_ssh_user=None,
    ssh_timeout=SSH_TIMEOUT,
):
    """Gets GPU info from a single host via nvidia-smi over SSH."""
    gpus_data = []

    nvidia_smi_cmd_on_remote = (
        "nvidia-smi --query-gpu=index,name,uuid,utilization.gpu,memory.total,memory.used,temperature.gpu,power.draw,power.limit "
        "--format=csv,noheader,nounits"
    )
    # Command to get UUIDs of GPUs with active compute applications
    nvidia_smi_compute_apps_cmd_on_remote = (
        "nvidia-smi --query-compute-apps=gpu_uuid --format=csv,noheader,nounits"
    )

    ssh_target = f"{cli_ssh_user}@{hostname}" if cli_ssh_user else hostname

    ssh_command_parts_main = [
        "ssh",
        "-T",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=10",
        ssh_target,
        nvidia_smi_cmd_on_remote,
    ]
    ssh_command_parts_compute_apps = [
        "ssh",
        "-T",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=5",  # Shorter timeout for this one
        ssh_target,
        nvidia_smi_compute_apps_cmd_on_remote,
    ]

    try:
        process_main = subprocess.run(
            ssh_command_parts_main, capture_output=True, text=True, timeout=ssh_timeout
        )

        if process_main.returncode != 0:
            error_msg = process_main.stderr.strip()
            if not error_msg:
                error_msg = (
                    f"SSH/Remote command failed (code {process_main.returncode})"
                )
            else:
                error_msg = error_msg.splitlines()[0]

            if "Permission denied" in error_msg or "publickey" in error_msg:
                error_msg = "Permission denied (check SSH key)"
            elif (
                "Could not resolve hostname" in error_msg
                or "Name or service not known" in error_msg
            ):
                error_msg = "Could not resolve hostname"
            elif "connect to host" in error_msg and "Connection timed out" in error_msg:
                error_msg = "Connection timed out"
            elif "nvidia-smi: command not found" in process_main.stderr:
                return [{"host": hostname, "error": "nvidia-smi not found on host"}]
            return [{"host": hostname, "error": error_msg}]

        output_main = process_main.stdout.strip()
        if not output_main:
            return [{"host": hostname, "error": "nvidia-smi returned no GPU data"}]

        # Get GPUs with active compute processes
        gpu_uuids_with_compute_apps = set()
        try:
            process_compute_apps = subprocess.run(
                ssh_command_parts_compute_apps,
                capture_output=True,
                text=True,
                timeout=10,
            )
            if (
                process_compute_apps.returncode == 0
                and process_compute_apps.stdout.strip()
            ):
                for line in process_compute_apps.stdout.strip().splitlines():
                    gpu_uuids_with_compute_apps.add(line.strip())
        except subprocess.TimeoutExpired:
            # Log or handle timeout for compute apps query, but don't fail the main data
            CONSOLE.print(
                f"[dim yellow]Timeout querying compute apps on {hostname}[/dim yellow]",
                end=" ",
            )
        except Exception:
            # Log or handle other errors for compute apps query
            CONSOLE.print(
                f"[dim yellow]Error querying compute apps on {hostname}[/dim yellow]",
                end=" ",
            )

        for line in output_main.splitlines():
            parts = line.split(", ")
            if len(parts) < 9:
                gpus_data.append(
                    {"host": hostname, "error": f"nvidia-smi parse error: {line}"}
                )
                continue

            gpu_uuid = parts[2]
            gpu_info = {
                "host": hostname,
                "gpu_id": int(parts[0]),
                "name": parts[1],
                "uuid": gpu_uuid,
                "utilization": float(parts[3]),
                "memory_total": float(parts[4]),
                "memory_used": float(parts[5]),
                "temperature": float(parts[6]),
                "power_draw": float(parts[7])
                if parts[7].replace(".", "", 1).isdigit()
                else None,  # Handle 'N/A'
                "power_limit": float(parts[8])
                if parts[8].replace(".", "", 1).isdigit()
                else None,  # Handle 'N/A'
                "error": None,
                "has_compute_processes": gpu_uuid in gpu_uuids_with_compute_apps,
            }
            gpus_data.append(gpu_info)

    except subprocess.TimeoutExpired:
        return [{"host": hostname, "error": "SSH command timed out"}]
    except FileNotFoundError:  # For ssh command itself not found
        return [
            {
                "host": hostname,
                "error": "SSH command not found. Is OpenSSH client installed?",
            }
        ]
    except Exception as e:
        return [
            {
                "host": hostname,
                "error": f"SSH connection failed: {str(e).splitlines()[0]}",
            }
        ]

    if not gpus_data:  # Should be populated if output_main was not empty
        return [
            {
                "host": hostname,
                "error": "No GPU data processed despite nvidia-smi output",
            }
        ]

    return gpus_data


def generate_host_summary_table(
    all_host_data: list,  # This should be data specific to ONE cluster
    cluster_display_name: str,
    results_cache: dict,  # Contains host-level status like 'ok', 'error', 'updating'
    app_settings: dict,
) -> Table:
    table = Table(
        title=f"Cluster Overview: [bold cyan]{cluster_display_name}[/bold cyan]",
        show_lines=False,
        expand=True,
        box=rich.box.ROUNDED,  # Added box style
        show_edge=True         # Ensure edge is shown with box style
    )
    table.add_column(
        "Host", style=STYLE_HOST, min_width=18, ratio=2
    )  # Increased min_width for emoji
    table.add_column("GPUs (Busy/Total)", justify="center", ratio=1)
    table.add_column("Available GPU IDs", justify="left", ratio=1.5)
    table.add_column("Avg Util %", justify="right", ratio=1)
    table.add_column("Avg Mem %", justify="right", ratio=1)
    table.add_column("Avg Temp °C", justify="right", ratio=1)
    table.add_column("Total Power W", justify="right", ratio=1)
    table.add_column("GPU Types", justify="left", min_width=20, ratio=2)

    # Group data by host from the provided all_host_data (which is for this cluster)
    host_map = {}
    for (
        gpu_info
    ) in all_host_data:  # all_host_data is already filtered for the current cluster
        host = gpu_info.get("host", "Unknown Host")
        if host not in host_map:
            host_map[host] = {"gpus": [], "error": None, "has_gpu_level_error": False}

        # Check for host-level error (where gpu_id is None but error exists for the host)
        if gpu_info.get("error") and gpu_info.get("gpu_id") is None:
            host_map[host]["error"] = gpu_info["error"]
        elif gpu_info.get("gpu_id") is not None:  # Actual GPU-level data
            host_map[host]["gpus"].append(gpu_info)
            if gpu_info.get("error"):  # Error specific to this GPU
                host_map[host]["has_gpu_level_error"] = True
        # If it's a host-level entry without gpu_id but also without error, it's ignored here
        # (e.g. an entry just confirming host reachability without GPU data - not typical for this structure)

    sorted_hosts = sorted(host_map.keys(), key=_natural_sort_key_for_host)

    for host in sorted_hosts:
        data = host_map[host]
        host_status_emoji = app_settings.get(
            "status_emoji_ok", DEFAULT_SETTINGS["status_emoji_ok"]
        )
        host_display_name = host

        # Use results_cache to get overall host status emoji if available
        # This reflects SSH reachability and nvidia-smi command success primarily
        cached_host_status = results_cache.get(host)
        if cached_host_status:
            if cached_host_status.get("status") == "error":
                host_status_emoji = app_settings.get(
                    "status_emoji_error", DEFAULT_SETTINGS["status_emoji_error"]
                )
                data["error"] = cached_host_status.get(
                    "error_message", "Host error from cache"
                )  # Prioritize cached error
            elif cached_host_status.get("status") == "updating":
                host_status_emoji = app_settings.get(
                    "status_emoji_updating", DEFAULT_SETTINGS["status_emoji_updating"]
                )
                data["error"] = "Updating..."  # Indicate updating status
            # 'ok' status from cache means we proceed to check GPU data for warnings

        if (
            data["error"] and not data["gpus"]
        ):  # Host-level error and no GPU data to parse
            host_display_name = f"{host_status_emoji} {host}"
            
            # Determine a concise error message for the table cell
            error_message_for_cell = data["error"]
            # Keywords indicating a general SSH communication problem
            ssh_comm_keywords = ["connection", "resolve", "timeout", "permission", "unreachable", "batchmode", "port"]
            if any(kw in error_message_for_cell.lower() for kw in ssh_comm_keywords):
                error_message_for_cell = "SSH Comms Error"
            # Other specific errors like "nvidia-smi not found" will be shown but truncated by ellipsis if too long

            table.add_row(
                host_display_name,
                Text("N/A", style=STYLE_ERROR), # GPUs (Busy/Total)
                Text(error_message_for_cell, style=STYLE_ERROR, overflow="ellipsis"), # Available GPU IDs
                "-", "-", "-", "-", "-",
            )
            continue

        gpus_on_host = data["gpus"]
        if not gpus_on_host and not data["error"]:
            # This case means host was reachable, nvidia-smi ran, but returned no GPUs (e.g. no NVIDIA card)
            # or it's an old entry from a previous cycle and current is 'updating'
            if cached_host_status and cached_host_status.get("status") == "updating":
                host_status_emoji = app_settings.get(
                    "status_emoji_updating", DEFAULT_SETTINGS["status_emoji_updating"]
                )
                no_gpu_message = "Updating..."
            else:
                host_status_emoji = app_settings.get(
                    "status_emoji_unreachable",
                    DEFAULT_SETTINGS["status_emoji_unreachable"],
                )  # Or a specific 'no gpus found' emoji
                no_gpu_message = "No GPU data reported"
            host_display_name = f"{host_status_emoji} {host}"
            table.add_row(
                host_display_name,
                Text("0/0", style="dim"),
                Text(no_gpu_message, style=STYLE_WARNING),
                "-",
                "-",
                "-",
                "-",
                "-",
            )
            continue

        total_gpus_on_host = len(gpus_on_host)
        gpus_with_compute_processes_count = 0
        available_gpu_ids = []
        has_gpu_warnings = False  # For temperature warnings on individual GPUs
        has_gpu_critical_issues = data["has_gpu_level_error"]  # For explicit GPU errors

        total_util = 0
        total_mem_per = 0
        total_temp = 0
        total_power = 0
        valid_power_readings = 0
        gpus_contributing_to_averages = 0
        gpu_names = set()

        for gpu in gpus_on_host:
            if gpu.get("error"):  # Error for a specific GPU
                # This GPU won't contribute to averages or available list
                # has_gpu_critical_issues is already true if this path is taken for any GPU
                continue

            gpus_contributing_to_averages += 1

            if gpu.get("has_compute_processes", False):
                gpus_with_compute_processes_count += 1
            else:
                available_gpu_ids.append(gpu.get("gpu_id"))

            # Check for temperature warnings for host status emoji
            if gpu.get("temperature", 0) >= app_settings.get(
                "temp_warn_threshold", DEFAULT_SETTINGS["temp_warn_threshold"]
            ):
                has_gpu_warnings = True
            if gpu.get("temperature", 0) >= app_settings.get(
                "temp_crit_threshold", DEFAULT_SETTINGS["temp_crit_threshold"]
            ):
                has_gpu_critical_issues = (
                    True  # High temp can also make host status critical
                )

            total_util += gpu.get("utilization", 0)
            if gpu.get("memory_total", 0) > 0:
                total_mem_per += (gpu.get("memory_used", 0) / gpu["memory_total"]) * 100
            total_temp += gpu.get("temperature", 0)
            if gpu.get("power_draw") is not None:
                total_power += gpu["power_draw"]
                valid_power_readings += 1
            gpu_names.add(gpu.get("name", "N/A"))

        # Determine final host status emoji, prioritizing critical, then warning
        if (
            has_gpu_critical_issues
        ):  # This includes individual GPU errors or critical temps
            host_status_emoji = app_settings.get(
                "status_emoji_error", DEFAULT_SETTINGS["status_emoji_error"]
            )
        elif has_gpu_warnings and host_status_emoji == app_settings.get(
            "status_emoji_ok", DEFAULT_SETTINGS["status_emoji_ok"]
        ):
            # Only set to warning if not already an error from host-level check or critical GPU issue
            host_status_emoji = app_settings.get(
                "status_emoji_warn", DEFAULT_SETTINGS["status_emoji_warn"]
            )
        # If host was 'updating' from cache, it remains 'updating' unless critical issues found
        elif (
            cached_host_status
            and cached_host_status.get("status") == "updating"
            and not has_gpu_critical_issues
        ):
            host_status_emoji = app_settings.get(
                "status_emoji_updating", DEFAULT_SETTINGS["status_emoji_updating"]
            )

        host_display_name = f"{host_status_emoji} {host}"

        avg_util_str = (
            f"{total_util / gpus_contributing_to_averages:.1f}"
            if gpus_contributing_to_averages > 0
            else "N/A"
        )
        avg_mem_str = (
            f"{total_mem_per / gpus_contributing_to_averages:.1f}"
            if gpus_contributing_to_averages > 0
            else "N/A"
        )
        avg_temp_str = (
            f"{total_temp / gpus_contributing_to_averages:.1f}"
            if gpus_contributing_to_averages > 0
            else "N/A"
        )
        total_power_str = f"{total_power:.0f}" if valid_power_readings > 0 else "N/A"

        busy_total_str = ""
        if (
            host_status_emoji
            == app_settings.get(
                "status_emoji_error", DEFAULT_SETTINGS["status_emoji_error"]
            )
            and gpus_contributing_to_averages == 0
        ):
            busy_total_str = Text("ERR", style=STYLE_ERROR)
        elif (
            total_gpus_on_host == 0
        ):  # Should be caught by 'No GPU data' earlier, but as a safeguard
            busy_total_str = Text("0/0", style="dim")
        elif gpus_with_compute_processes_count > 0:
            busy_total_str = f"{app_settings.get('busy_gpu_emoji', DEFAULT_SETTINGS['busy_gpu_emoji'])} {gpus_with_compute_processes_count}/{total_gpus_on_host}"
        else:
            busy_total_str = f"{app_settings.get('idle_gpu_emoji', DEFAULT_SETTINGS['idle_gpu_emoji'])} {gpus_with_compute_processes_count}/{total_gpus_on_host}"

        gpu_types_str = ", ".join(sorted(list(gpu_names))) if gpu_names else "N/A"
        available_gpu_ids_str = _format_gpu_ids_to_ranges(
            sorted(list(set(available_gpu_ids)))
        )

        table.add_row(
            host_display_name,
            busy_total_str,
            available_gpu_ids_str,
            avg_util_str,
            avg_mem_str,
            avg_temp_str,
            total_power_str,
            Text(gpu_types_str, style=STYLE_GPU_NAME, overflow="ellipsis"),
        )
    return table


def generate_problem_gpus_table(
    all_host_data: list, cluster_display_name: str, app_settings: dict
) -> Table | None:
    table = Table(
        title=f"Problematic GPUs - {cluster_display_name}",
        expand=True,
        show_lines=True,
        show_edge=True,
        box=rich.box.ROUNDED # Changed from None
    )
    table.add_column("Host", style=STYLE_HOST, min_width=12)
    table.add_column("GPU ID", justify="center", min_width=4)
    table.add_column("GPU Name", style=STYLE_GPU_NAME, min_width=20)
    table.add_column("Util (%)", justify="right", min_width=8)
    table.add_column("Temp (°C)", justify="right", min_width=8)
    table.add_column("Issue / Error", justify="left", min_width=25, overflow="fold")

    problem_gpus = []
    # flat_data = [gpu for host_gpus_list in all_host_data for gpu in host_gpus_list] # This was incorrect as all_host_data is already flat

    for gpu_item in all_host_data:  # Iterate directly over the already flat list
        if not isinstance(gpu_item, dict):
            # This case should ideally not happen if data collection is correct
            # Optionally, log this occurrence
            CONSOLE.print(
                f"[bold red]Warning: Encountered non-dictionary item in problem GPU data: {type(gpu_item)} - {str(gpu_item)[:100]}[/bold red]"
            )
            continue

        issues = []
        # Check for errors first
        if gpu_item.get("error"):
            issues.append(Text(f"Error: {gpu_item.get('error')}", style=STYLE_ERROR))
        else:
            # If no direct error, check for temperature issues
            temp = gpu_item.get("temperature", 0)
            if temp >= app_settings.get(
                "temp_warn_threshold", DEFAULT_SETTINGS["temp_warn_threshold"]
            ):
                style = (
                    STYLE_CRITICAL
                    if temp
                    >= app_settings.get(
                        "temp_crit_threshold", DEFAULT_SETTINGS["temp_crit_threshold"]
                    )
                    else STYLE_WARNING
                )
                issues.append(Text(f"High Temp: {temp:.1f}°C", style=style))

            # Note: High utilization is no longer considered a "problem" for this table by itself.
            # It will still be reflected in the detailed table and host summary busy count.

        if issues:
            problem_gpus.append({**gpu_item, "issues_text": Group(*issues)})

    if not problem_gpus:
        return None

    sorted_problem_gpus = sorted(problem_gpus, key=_natural_sort_key_for_gpu)

    for gpu in sorted_problem_gpus:
        temp_text = Text(f"{gpu.get('temperature', 0):.1f}°C")
        if gpu.get("temperature", 0) >= app_settings.get(
            "temp_crit_threshold", DEFAULT_SETTINGS["temp_crit_threshold"]
        ):
            temp_text.stylize(STYLE_CRITICAL)
        elif gpu.get("temperature", 0) >= app_settings.get(
            "temp_warn_threshold", DEFAULT_SETTINGS["temp_warn_threshold"]
        ):
            temp_text.stylize(STYLE_WARNING)

        if gpu.get("error"):
            temp_text = Text("-", style=STYLE_ERROR)

        table.add_row(
            Text(gpu.get("host", "N/A")),
            Text(str(gpu.get("gpu_id", "-")), justify="center"),
            Text(gpu.get("name", "N/A"), style=STYLE_GPU_NAME),
            temp_text,
            gpu.get("issues_text", Text("Unknown Issue", style=STYLE_ERROR)),
        )
    return table


def generate_detailed_gpu_table(
    all_host_data: list, cluster_display_name: str, app_settings: dict
) -> Table:
    table = Table(
        title=f"All GPUs Detailed - {cluster_display_name} (Updated: {time.strftime('%Y-%m-%d %H:%M:%S')})",
        expand=True,
        show_lines=True,
        show_edge=True,
        box=rich.box.ROUNDED # Changed from None
    )
    table.add_column("Host", style=STYLE_HOST, justify="left", min_width=12)
    table.add_column("GPU ID", justify="center", min_width=4)
    table.add_column(
        "GPU Name", style=STYLE_GPU_NAME, justify="left", min_width=22, overflow="fold"
    )
    table.add_column("Util (%)", justify="right", min_width=8)
    table.add_column("Temp (°C)", justify="right", min_width=8)
    table.add_column("Mem (MiB)", justify="right", min_width=12)
    table.add_column("Pwr (W)", justify="right", min_width=12)
    table.add_column("Status/Error", justify="left", min_width=25, overflow="fold")

    # all_host_data is already a flat list. The previous list comprehension was incorrect.
    sorted_data = sorted(all_host_data, key=_natural_sort_key_for_gpu)

    for gpu in sorted_data:
        host = gpu.get("host", "N/A")
        if gpu.get("error"):
            table.add_row(
                Text(host),
                Text(str(gpu.get("gpu_id", "-")), justify="center"),
                Text(gpu.get("name", "")),
                "",
                "",
                "",
                "",
                Text(gpu["error"], style=STYLE_ERROR),
            )
            continue

        util = gpu.get("utilization", 0.0)
        temp = gpu.get("temperature", 0.0)

        temp_style = STYLE_OK
        if temp >= app_settings.get(
            "temp_crit_threshold", DEFAULT_SETTINGS["temp_crit_threshold"]
        ):
            temp_style = STYLE_CRITICAL
        elif temp >= app_settings.get(
            "temp_warn_threshold", DEFAULT_SETTINGS["temp_warn_threshold"]
        ):
            temp_style = STYLE_WARNING

        mem_str = (
            f"{gpu.get('memory_used', 0.0):.0f}/{gpu.get('memory_total', 1.0):.0f}"
        )
        power_draw, power_limit = gpu.get("power_draw"), gpu.get("power_limit")
        power_str = "N/A"
        if power_draw is not None and power_limit is not None:
            power_str = f"{power_draw:.0f}/{power_limit:.0f}"
        elif power_draw is not None:
            power_str = f"{power_draw:.0f}/---"

        table.add_row(
            Text(host),
            Text(str(gpu.get("gpu_id", "-")), justify="center"),
            Text(gpu.get("name", "N/A"), style=STYLE_GPU_NAME),
            Text(f"{util:.1f}"),
            Text(f"{temp:.1f}", style=temp_style),
            mem_str,
            power_str,
            Text("OK"),
        )
    return table


def ensure_config_dir_exists(config_dir_path: str):
    """Ensures the configuration directory exists."""
    try:
        os.makedirs(config_dir_path, exist_ok=True)
    except OSError as e:
        CONSOLE.print(
            f"[bold red]Error: Could not create config directory {config_dir_path}: {e}[/bold red]"
        )
        sys.exit(1)


def list_cluster_configs(config_dir):
    ensure_config_dir_exists(config_dir)
    try:
        files = [f for f in os.listdir(config_dir) if f.endswith((".yaml", ".yml"))]
        if not files:
            CONSOLE.print(f"No cluster configuration files found in '{config_dir}'.")
            CONSOLE.print(
                "Use 'gpu-cluster-monitor add-cluster <new_cluster_name>' to create one."
            )
            return []
        CONSOLE.print(
            f"[bold]Available cluster configurations in '{config_dir}':[/bold]"
        )
        for f in sorted(files):
            CONSOLE.print(f"  - {os.path.splitext(f)[0]}")
        return [os.path.splitext(f)[0] for f in sorted(files)]
    except (
        FileNotFoundError
    ):  # Should be caught by ensure_config_dir_exists, but as fallback
        CONSOLE.print(
            f"Config directory '{config_dir}' not found (this should not happen)."
        )
        return []


def add_cluster_interactive(config_dir: str, cluster_name: str):
    """Interactively adds a new cluster configuration."""
    ensure_config_dir_exists(config_dir)
    # Validate cluster_name to prevent path traversal or invalid filenames
    if (
        not cluster_name
        or "/" in cluster_name
        or "\\" in cluster_name
        or cluster_name.startswith(".")
    ):
        CONSOLE.print(
            f"[bold red]Invalid cluster name: '{cluster_name}'. Cannot contain slashes or be hidden.[/bold red]"
        )
        return

    cluster_file_path = os.path.join(config_dir, f"{cluster_name}.yaml")

    if os.path.exists(cluster_file_path):
        CONSOLE.print(
            f"[bold yellow]Cluster '{cluster_name}' already exists at {cluster_file_path}.[/bold yellow]"
        )
        overwrite = input("Overwrite? [y/N]: ").strip().lower()
        if overwrite != "y":
            CONSOLE.print("Aborted.")
            return

    CONSOLE.print(f"Adding new cluster: [bold cyan]{cluster_name}[/bold cyan]")
    display_name = input(
        f"Enter a display name for the cluster (or press Enter to use '{cluster_name}'): "
    ).strip()
    if not display_name:
        display_name = cluster_name

    hosts_list = []
    CONSOLE.print(
        "Enter hostnames for this cluster, one per line. Press Enter on an empty line to finish."
    )
    while True:
        host_entry = input(f"Host #{len(hosts_list) + 1}: ").strip()
        if not host_entry:
            break
        hosts_list.append(host_entry)

    if not hosts_list:
        CONSOLE.print("[bold red]No hosts provided. Aborting.[/bold red]")
        return

    config_data = {"cluster_name": display_name, "hosts": hosts_list}

    try:
        with open(cluster_file_path, "w") as f:
            yaml.dump(config_data, f, sort_keys=False, indent=2)
        CONSOLE.print(
            f"[bold green]Cluster '{cluster_name}' successfully saved to {cluster_file_path}[/bold green]"
        )
    except Exception as e:
        CONSOLE.print(f"[bold red]Error saving cluster configuration: {e}[/bold red]")


def remove_cluster_interactive(config_dir: str, cluster_name: str):
    """Interactively removes a cluster configuration."""
    ensure_config_dir_exists(config_dir)

    actual_file_path = None
    for ext in [".yaml", ".yml"]:
        test_path = os.path.join(config_dir, f"{cluster_name}{ext}")
        if os.path.exists(test_path):
            actual_file_path = test_path
            break

    if not actual_file_path:
        CONSOLE.print(
            f"[bold red]Error: Cluster configuration '{cluster_name}' not found in {config_dir}.[/bold red]"
        )
        list_cluster_configs(config_dir)
        return

    CONSOLE.print(
        f"About to remove cluster: [bold yellow]{cluster_name}[/bold yellow] from [cyan]{actual_file_path}[/cyan]"
    )
    confirm = (
        input("Are you sure you want to delete this cluster configuration? [y/N]: ")
        .strip()
        .lower()
    )

    if confirm == "y":
        try:
            os.remove(actual_file_path)
            CONSOLE.print(
                f"[bold green]Cluster '{cluster_name}' removed successfully.[/bold green]"
            )
        except OSError as e:
            CONSOLE.print(
                f"[bold red]Error removing cluster file {actual_file_path}: {e}[/bold red]"
            )
    else:
        CONSOLE.print("Removal aborted.")


def setup_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="GPU Cluster Monitor CLI. Manages and displays GPU stats from remote hosts.",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument(
        "-c",
        "--config",
        type=str,
        default="~/.gpu-cluster-monitor/clusters.yaml",
        help="Path to the cluster configuration YAML file. Default: ~/.gpu-cluster-monitor/clusters.yaml",
    )
    parser.add_argument(
        "--config-dir",
        type=str,
        default=os.path.expanduser("~/.gpu-cluster-monitor"),
        help="Path to the configuration directory. Default: ~/.gpu-cluster-monitor",
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # 'monitor' subcommand
    monitor_parser = subparsers.add_parser(
        "monitor",
        help="Run the GPU monitoring dashboard (default if no command specified).",
    )
    monitor_parser.add_argument(
        "cluster_name",
        nargs="?",
        help="Name of the cluster to monitor (from configuration file). Runs all if not specified.",
    )
    monitor_parser.add_argument(
        "-i",
        "--interval",
        type=int,
        default=None,  # Default is handled by app_settings, None here means not overridden by CLI arg
        help=(
            f"Refresh interval (seconds). "
            f"Default: {DEFAULT_SETTINGS['refresh_interval']} (configurable via {GLOBAL_SETTINGS_FILENAME} in config dir)"
        ),
    )
    monitor_parser.add_argument(
        "--show-all-gpus",
        action="store_true",
        help="Show a detailed table of all GPUs, regardless of their status.",
    )
    monitor_parser.add_argument(
        "--ssh-debug",
        action="store_true",
        help="Enable detailed SSH command debugging output.",
    )
    monitor_parser.set_defaults(func=execute_monitor_command)

    # 'settings' subcommand
    settings_parser = subparsers.add_parser(
        "settings", help="Manage application settings."
    )
    settings_subparsers = settings_parser.add_subparsers(
        dest="settings_action", help="Settings actions", required=True
    )
    init_parser = settings_subparsers.add_parser(
        "init",
        help=f"Create a default {GLOBAL_SETTINGS_FILENAME} file in the config directory.",
    )
    init_parser.set_defaults(func=execute_settings_init_command)

    return parser


# --- Command Execution Functions ---
def create_default_settings_file(config_dir: str, settings_file_path: str) -> bool:
    """Creates a default settings.yaml file if it doesn't exist. Returns True on success/existence, False on error."""
    ensure_config_dir_exists(config_dir)
    if os.path.exists(settings_file_path):
        CONSOLE.print(
            f"[yellow]Skipped:[/] Settings file already exists at [cyan]{settings_file_path}[/].\n"
            f"If you want to regenerate it, please delete the existing file first."
        )
        return True  # Considered a success in terms of having a settings file
    try:
        with open(settings_file_path, "w") as f:
            yaml.dump(
                DEFAULT_SETTINGS, f, sort_keys=False, indent=2, default_flow_style=False
            )
        CONSOLE.print(
            f"[green]Success:[/] Default settings file created at [cyan]{settings_file_path}[/]."
        )
        CONSOLE.print("You can now customize it to your preferences.")
        return True
    except IOError as e:
        CONSOLE.print(
            f"[red]Error:[/] Could not write settings file to [cyan]{settings_file_path}[/]: {e}"
        )
        return False
    except yaml.YAMLError as e:
        CONSOLE.print(
            f"[red]Error:[/] Could not serialize default settings to YAML: {e}"
        )
        return False


def execute_settings_init_command(args: argparse.Namespace):
    """Handles the 'settings init' command."""
    config_dir = os.path.expanduser(args.config_dir)
    settings_file_path = os.path.join(config_dir, GLOBAL_SETTINGS_FILENAME)
    create_default_settings_file(config_dir, settings_file_path)


def execute_monitor_command(args: argparse.Namespace):
    """Handles the 'monitor' command, including loading settings and starting the monitor."""
    config_dir = os.path.expanduser(args.config_dir)
    ensure_config_dir_exists(config_dir)
    app_settings = load_global_settings(config_dir)  # Load settings first

    # Override refresh_interval from CLI args if provided
    if args.interval is not None:
        app_settings["refresh_interval"] = args.interval
    else:
        # Ensure refresh_interval is set from loaded settings or defaults
        app_settings["refresh_interval"] = app_settings.get(
            "refresh_interval", DEFAULT_SETTINGS["refresh_interval"]
        )

    cluster_config_path = os.path.expanduser(args.config)
    if not os.path.exists(cluster_config_path):
        CONSOLE.print(
            f"[red]Error:[/] Cluster configuration file not found at [cyan]{cluster_config_path}[/].\n"
            f"Please ensure it exists or provide the correct path using the -c/--config argument."
        )
        # Suggest creating settings if config dir might also be an issue
        if not os.path.exists(os.path.dirname(cluster_config_path)) or not os.listdir(
            os.path.dirname(cluster_config_path)
        ):
            CONSOLE.print(
                "You can create a default settings file using: gpu-monitor settings init"
            )
        return

    try:
        with open(cluster_config_path, "r") as f:
            config = yaml.safe_load(f)
            if not config or "clusters" not in config:
                CONSOLE.print(
                    f"[red]Error:[/] Invalid cluster configuration in [cyan]{cluster_config_path}[/]. "
                    f"Ensure it contains a 'clusters' key with a list of cluster definitions."
                )
                return
    except yaml.YAMLError as e:
        CONSOLE.print(
            f"[red]Error:[/] Could not parse cluster configuration file [cyan]{cluster_config_path}[/]: {e}"
        )
        return
    except IOError as e:
        CONSOLE.print(
            f"[red]Error:[/] Could not read cluster configuration file [cyan]{cluster_config_path}[/]: {e}"
        )
        return

    clusters_to_monitor = []
    display_name_map = {}

    if args.cluster_name:
        # Monitor a specific cluster
        found_cluster = False
        for cluster_info in config.get("clusters", []):
            if cluster_info.get("name") == args.cluster_name:
                clusters_to_monitor.append(cluster_info)
                display_name_map[cluster_info["name"]] = cluster_info.get(
                    "display_name", cluster_info["name"]
                )
                found_cluster = True
                break
        if not found_cluster:
            CONSOLE.print(
                f"[red]Error:[/] Cluster '[bold]{args.cluster_name}[/bold]' not found in configuration."
            )
            available_clusters = [
                c.get("name", "Unnamed") for c in config.get("clusters", [])
            ]
            if available_clusters:
                CONSOLE.print(f"Available clusters: {', '.join(available_clusters)}")
            return
    else:
        # Monitor all clusters
        clusters_to_monitor = config.get("clusters", [])
        if not clusters_to_monitor:
            CONSOLE.print(
                "[yellow]Warning:[/] No clusters defined in the configuration file."
            )
            return
        for cluster_info in clusters_to_monitor:
            display_name_map[cluster_info["name"]] = cluster_info.get(
                "display_name", cluster_info["name"]
            )

    # This is the core monitoring loop, previously part of run_monitor
    results_cache = {}
    all_host_data_lock = threading.Lock()  # For thread-safe appends to all_host_data

    # --- Start of fetch_gpu_data_for_host definition ---
    def fetch_gpu_data_for_host(
        host_name_param: str,
        app_settings_param: dict,
        _ssh_debug_param: bool,  # Currently unused by get_gpu_info_subprocess
        ssh_user_param: str | None,
        _ssh_key_param: str | None,  # Currently unused by get_gpu_info_subprocess
        _ssh_port_param: int,  # Currently unused by get_gpu_info_subprocess
        cluster_display_name_param: str,
    ):
        # results_cache and all_host_data are accessed from the outer scope
        # Initial status in cache
        results_cache[host_name_param] = {
            "status": "updating",
            "emoji": app_settings_param.get(
                "status_emoji_updating", DEFAULT_SETTINGS["status_emoji_updating"]
            ),
            "error_message": None,
            "last_updated": time.time(),
            "cluster_display_name": cluster_display_name_param,
        }

        ssh_timeout_val = app_settings_param.get(
            "ssh_timeout", DEFAULT_SETTINGS["ssh_timeout"]
        )

        # Call the actual data fetching function
        host_gpu_data_list = get_gpu_info_subprocess(
            host_name_param,
            cli_ssh_user=ssh_user_param,
            ssh_timeout=ssh_timeout_val,
        )

        host_had_error = False
        specific_error_message = "Unknown error retrieving data"

        if not host_gpu_data_list:
            host_had_error = True
            specific_error_message = (
                "No data returned from host check (get_gpu_info_subprocess)"
            )
            host_gpu_data_list = [
                {"host": host_name_param, "error": specific_error_message}
            ]
        elif isinstance(host_gpu_data_list, list) and len(host_gpu_data_list) > 0:
            first_item = host_gpu_data_list[0]
            if (
                isinstance(first_item, dict)
                and first_item.get("error")
                and first_item.get("host") == host_name_param
                and first_item.get("gpu_id") is None
            ):
                host_had_error = True
                specific_error_message = first_item["error"]
        else:  # Should not happen if get_gpu_info_subprocess is well-behaved
            host_had_error = True
            specific_error_message = f"Unexpected data format from {host_name_param}: {type(host_gpu_data_list)}"
            host_gpu_data_list = [
                {"host": host_name_param, "error": specific_error_message}
            ]

        if host_had_error:
            results_cache[host_name_param].update(
                {
                    "status": "error",
                    "emoji": app_settings_param.get(
                        "status_emoji_error", DEFAULT_SETTINGS["status_emoji_error"]
                    ),
                    "error_message": specific_error_message,
                    "last_updated": time.time(),
                }
            )
        else:
            results_cache[host_name_param].update(
                {
                    "status": "ok",
                    "emoji": app_settings_param.get(
                        "status_emoji_ok", DEFAULT_SETTINGS["status_emoji_ok"]
                    ),
                    "error_message": None,
                    "last_updated": time.time(),
                }
            )

        with all_host_data_lock:
            for item_data in host_gpu_data_list:
                if "host" not in item_data:
                    item_data["host"] = host_name_param  # Ensure host key is present
                all_host_data.append(item_data)

    # --- End of fetch_gpu_data_for_host definition ---

    with Live(console=CONSOLE, refresh_per_second=10, transient=True) as live:
        while True:
            all_host_data = []  # Data from all hosts in the current iteration, reset each cycle
            active_threads = []

            # --- Prepare renderables list, starting with the main title ---
            renderables = []
            current_time_str = time.strftime("%Y-%m-%d %H:%M:%S %Z")
            title_text = Text(
                f"GPU Cluster Monitor Dashboard - Last Updated: {current_time_str}",
                justify="center",
            )
            dashboard_title_panel = Panel(
                title_text, border_style="dim blue", padding=(0, 1)
            )
            renderables.append(dashboard_title_panel)
            # --- End of main title panel ---

            for cluster_info in clusters_to_monitor:
                cluster_name = cluster_info["name"]
                cluster_display_name = display_name_map[cluster_name]
                hosts = cluster_info.get("hosts", [])
                ssh_user = cluster_info.get("ssh_user")
                ssh_key = cluster_info.get("ssh_key")
                ssh_port = cluster_info.get("ssh_port", 22)

                for host_entry in hosts:
                    host_name = (
                        host_entry
                        if isinstance(host_entry, str)
                        else host_entry.get("name")
                    )
                    if not host_name:
                        CONSOLE.print(
                            f"[yellow]Warning:[/] Skipping host with no name in cluster {cluster_name}."
                        )
                        continue

                    current_ssh_user = ssh_user
                    current_ssh_key = ssh_key
                    current_ssh_port = ssh_port
                    if isinstance(host_entry, dict):
                        current_ssh_user = host_entry.get("ssh_user", ssh_user)
                        current_ssh_key = host_entry.get("ssh_key", ssh_key)
                        current_ssh_port = host_entry.get("ssh_port", ssh_port)

                    thread = threading.Thread(
                        target=fetch_gpu_data_for_host,  # Now defined locally
                        args=(
                            host_name,
                            app_settings,  # results_cache and all_host_data accessed via closure
                            args.ssh_debug,
                            current_ssh_user,
                            current_ssh_key,
                            current_ssh_port,
                            cluster_display_name,
                        ),
                    )
                    active_threads.append(thread)
                    thread.start()

            for thread in active_threads:
                thread.join()

            # Generate summary table for each cluster
            for cluster_info in clusters_to_monitor:
                cluster_name = cluster_info["name"]
                cluster_display_name = display_name_map[cluster_name]

                current_cluster_host_names = []  # Collect host names for the current cluster
                for host_entry in cluster_info.get("hosts", []):
                    host_name_for_filter = (
                        host_entry
                        if isinstance(host_entry, str)
                        else host_entry.get("name")
                    )
                    if host_name_for_filter:
                        current_cluster_host_names.append(host_name_for_filter)
                current_cluster_host_names_set = set(current_cluster_host_names)

                current_cluster_gpu_data = [
                    gpu_data
                    for gpu_data in all_host_data
                    if gpu_data.get("host") in current_cluster_host_names_set
                ]

                summary_table = generate_host_summary_table(
                    current_cluster_gpu_data,  # Arg 1: Filtered GPU data for this cluster
                    cluster_display_name,  # Arg 2
                    results_cache,  # Arg 3
                    app_settings,  # Arg 4
                )
                renderables.append(summary_table)

            # Generate Problem GPUs table (across all monitored clusters)
            problem_table = generate_problem_gpus_table(
                all_host_data, "All Monitored Clusters", app_settings
            )
            if problem_table:
                renderables.append(problem_table)

            if args.show_all_gpus:
                detailed_table = generate_detailed_gpu_table(
                    all_host_data, "All Monitored Clusters", app_settings
                )
                renderables.append(detailed_table)

            if not renderables:
                # This can happen if no clusters are defined, or all are unreachable and no data is collected
                panel_content = "No data to display. Check cluster configurations and host reachability."
                if not config.get("clusters"):
                    panel_content = "No clusters defined in configuration. Add clusters to your clusters.yaml."
                elif (
                    not clusters_to_monitor
                ):  # Specific cluster name given but not found
                    panel_content = (
                        f"Cluster '{args.cluster_name}' not found or has no hosts."
                    )

                renderables.append(
                    Panel(
                        Text(panel_content, justify="center"),
                        title="Status",
                        border_style="dim",
                    )
                )

            # Update the live display with all generated tables/panels
            live.update(Group(*renderables))

            time.sleep(app_settings["refresh_interval"])


# --- Main Entry Point ---
def main():
    parser = setup_arg_parser()
    args = parser.parse_args()

    if args.command is None:  # Default to monitor if no command is given
        # Ensure 'monitor' specific args (like interval, cluster_name) are available in 'args'
        # as if 'monitor' was explicitly typed. Argparse handles this if they are top-level optional args
        # or part of the default subcommand's parser.
        execute_monitor_command(args)
    elif hasattr(args, "func"):
        args.func(args)
    else:
        # This case should ideally not be reached if subparsers are configured correctly
        # (e.g. 'settings' subparser has 'required=True' for its actions).
        parser.print_help()


if __name__ == "__main__":
    main()

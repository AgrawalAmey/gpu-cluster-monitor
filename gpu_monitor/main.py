import subprocess
import shlex 
import time
import threading
from rich.live import Live
from rich.table import Table
from rich.console import Console, Group, RenderableType
from rich.text import Text
from rich.style import Style
from rich.panel import Panel
import argparse
import os
import yaml
import sys
import datetime

# --- Default Configuration ---
REFRESH_INTERVAL = 5
DEFAULT_CLUSTER_CONFIG_DIR = os.path.expanduser("~/.gpu-cluster-monitor")

# --- Thresholds for color coding ---
UTILIZATION_WARN_THRESHOLD = 75
UTILIZATION_CRIT_THRESHOLD = 90
TEMP_WARN_THRESHOLD = 75
TEMP_CRIT_THRESHOLD = 85

# --- Styles ---
STYLE_CRITICAL = Style(color="red", bold=True)
STYLE_WARNING = Style(color="yellow")
STYLE_OK = Style(color="green")
STYLE_ERROR = Style(color="bright_red", bold=True)
STYLE_HOST = Style(color="cyan", bold=True)
STYLE_GPU_NAME = Style(color="magenta")

CONSOLE = Console()

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
        return Text("None", style="dim") # Or an empty string if preferred

    # Ensure IDs are integers and sorted
    ids = sorted([int(gid) for gid in gpu_ids])

    ranges = []
    if not ids:
        return Text("None", style="dim")

    start_range = ids[0]
    for i in range(1, len(ids)):
        if ids[i] != ids[i-1] + 1:
            # End of a range or a single number
            if start_range == ids[i-1]:
                ranges.append(str(start_range))
            else:
                ranges.append(f"{start_range}-{ids[i-1]}")
            start_range = ids[i]
    
    # Add the last range or single number
    if start_range == ids[-1]:
        ranges.append(str(start_range))
    else:
        ranges.append(f"{start_range}-{ids[-1]}")
    
    return ", ".join(ranges)


def load_cluster_config(config_path):
    try:
        with open(config_path, 'r') as f:
            config = yaml.safe_load(f)
        return config
    except FileNotFoundError:
        CONSOLE.print(f"[bold red]Error: Config file not found at {config_path}[/bold red]")
        return None
    except yaml.YAMLError as e:
        CONSOLE.print(f"[bold red]Error parsing YAML config file {config_path}: {e}[/bold red]")
        return None
    except Exception as e:
        CONSOLE.print(f"[bold red]An unexpected error occurred while loading config {config_path}: {e}[/bold red]")
        return None

def get_gpu_info_subprocess(hostname, cli_ssh_user=None):
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
        "ssh", "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
        ssh_target, nvidia_smi_cmd_on_remote
    ]
    ssh_command_parts_compute_apps = [
        "ssh", "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", # Shorter timeout for this one
        ssh_target, nvidia_smi_compute_apps_cmd_on_remote
    ]

    try:
        process_main = subprocess.run(
            ssh_command_parts_main, capture_output=True, text=True, timeout=25
        )

        if process_main.returncode != 0:
            error_msg = process_main.stderr.strip()
            if not error_msg: error_msg = f"SSH/Remote command failed (code {process_main.returncode})"
            else: error_msg = error_msg.splitlines()[0]

            if "Permission denied" in error_msg: error_msg = "Permission denied (check SSH keys/agent, user)"
            elif "Could not resolve hostname" in error_msg or "Name or service not known" in error_msg: error_msg = "Could not resolve hostname"
            elif "connect to host" in error_msg and "Connection timed out" in error_msg: error_msg = "Connection timed out"
            elif "nvidia-smi: command not found" in process_main.stderr: return [{"host": hostname, "error": "nvidia-smi not found on host"}]
            return [{"host": hostname, "error": error_msg}]

        output_main = process_main.stdout.strip()
        if not output_main:
            return [{"host": hostname, "error": "nvidia-smi returned no GPU data"}]

        # Get GPUs with active compute processes
        gpu_uuids_with_compute_apps = set()
        try:
            process_compute_apps = subprocess.run(
                ssh_command_parts_compute_apps, capture_output=True, text=True, timeout=10
            )
            if process_compute_apps.returncode == 0 and process_compute_apps.stdout.strip():
                for line in process_compute_apps.stdout.strip().splitlines():
                    gpu_uuids_with_compute_apps.add(line.strip())
        except subprocess.TimeoutExpired:
            # Log or handle timeout for compute apps query, but don't fail the main data
            CONSOLE.print(f"[dim yellow]Timeout querying compute apps on {hostname}[/dim yellow]", end=' ')
        except Exception:
            # Log or handle other errors for compute apps query
            CONSOLE.print(f"[dim yellow]Error querying compute apps on {hostname}[/dim yellow]", end=' ')


        for line in output_main.splitlines():
            parts = line.split(', ')
            if len(parts) < 9:
                gpus_data.append({"host": hostname, "error": f"nvidia-smi parse error: {line}"})
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
                "power_draw": float(parts[7]) if parts[7].replace('.', '', 1).isdigit() else None, # Handle 'N/A'
                "power_limit": float(parts[8]) if parts[8].replace('.', '', 1).isdigit() else None, # Handle 'N/A'
                "error": None,
                "has_compute_processes": gpu_uuid in gpu_uuids_with_compute_apps
            }
            gpus_data.append(gpu_info)

    except subprocess.TimeoutExpired:
        return [{"host": hostname, "error": "SSH command timed out"}]
    except FileNotFoundError: # For ssh command itself not found
        return [{"host": hostname, "error": "SSH command not found. Is OpenSSH client installed?"}]
    except Exception as e:
        return [{"host": hostname, "error": f"SSH connection failed: {str(e).splitlines()[0]}"}]
    
    if not gpus_data: # Should be populated if output_main was not empty
        return [{"host": hostname, "error": "No GPU data processed despite nvidia-smi output"}]
        
    return gpus_data

def generate_host_summary_table(all_host_data: list, cluster_display_name: str) -> Table:
    table = Table(title=f"Cluster Overview: [bold cyan]{cluster_display_name}[/bold cyan]", show_lines=False, expand=True)
    table.add_column("Host", style=STYLE_HOST, min_width=18, ratio=2) # Increased min_width for emoji
    table.add_column("GPUs (Busy/Total)", justify="center", ratio=1)
    table.add_column("Available GPU IDs", justify="left", ratio=1.5)
    table.add_column("Avg Util %", justify="right", ratio=1)
    table.add_column("Avg Mem %", justify="right", ratio=1)
    table.add_column("Avg Temp °C", justify="right", ratio=1)
    table.add_column("Total Power W", justify="right", ratio=1)
    table.add_column("GPU Types", justify="left", min_width=20, ratio=2)

    # Group data by host
    host_map = {}
    for gpu_info in all_host_data:
        host = gpu_info.get("host", "Unknown Host")
        if host not in host_map:
            host_map[host] = {"gpus": [], "error": None, "has_gpu_level_error": False}
        
        if gpu_info.get("error") and not gpu_info.get("gpu_id") is not None: # Host-level error
            host_map[host]["error"] = gpu_info["error"]
        elif gpu_info.get("gpu_id") is not None: # GPU-level data
            host_map[host]["gpus"].append(gpu_info)
            if gpu_info.get("error"): # Error specific to this GPU
                host_map[host]["has_gpu_level_error"] = True
    
    sorted_hosts = sorted(host_map.keys(), key=_natural_sort_key_for_host)

    for host in sorted_hosts:
        data = host_map[host]
        host_status_emoji = "✅"
        host_display_name = host

        if data["error"]: # Host-level error
            host_status_emoji = "❌"
            host_display_name = f"{host_status_emoji} {host}"
            table.add_row(host_display_name, Text("N/A", style=STYLE_ERROR), Text(data["error"], style=STYLE_ERROR), "-", "-", "-", "-", "-")
            continue

        gpus_on_host = data["gpus"]
        if not gpus_on_host:
            host_status_emoji = "❓" # Or some other indicator for no GPU data
            host_display_name = f"{host_status_emoji} {host}"
            table.add_row(host_display_name, Text("0/0", style="dim"), Text("No GPU data", style=STYLE_WARNING), "-", "-", "-", "-", "-")
            continue

        total_gpus = len(gpus_on_host)
        busy_gpu_count = 0
        # problematic_gpus_count = 0 # Replaced by host_status_emoji logic
        has_gpu_warnings = False

        total_util = 0
        total_mem_per = 0
        total_temp = 0
        total_power = 0
        valid_power_readings = 0
        gpu_names = set()
        available_gpu_ids = []

        for gpu in gpus_on_host:
            if gpu.get("error"):
                host_status_emoji = "❌" # GPU-level error also makes host critical
                # problematic_gpus_count += 1 # Not directly used in column anymore
                continue # Don't include errored GPUs in averages or available list
            
            if gpu.get("utilization", 0) >= UTILIZATION_WARN_THRESHOLD or \
               gpu.get("temperature", 0) >= TEMP_WARN_THRESHOLD:
                has_gpu_warnings = True

            if gpu.get("has_compute_processes", False):
                busy_gpu_count += 1
            else:
                available_gpu_ids.append(gpu.get("gpu_id")) # Collect non-busy GPU IDs
            
            total_util += gpu.get("utilization", 0)
            if gpu.get("memory_total", 0) > 0:
                total_mem_per += (gpu.get("memory_used", 0) / gpu["memory_total"]) * 100
            total_temp += gpu.get("temperature", 0)
            if gpu.get("power_draw") is not None:
                total_power += gpu["power_draw"]
                valid_power_readings += 1
            gpu_names.add(gpu.get("name", "N/A"))
        
        non_errored_gpus = total_gpus - sum(1 for gpu in gpus_on_host if gpu.get("error"))
        avg_util_str = f"{total_util / non_errored_gpus:.1f}" if non_errored_gpus > 0 else "N/A"
        avg_mem_str = f"{total_mem_per / non_errored_gpus:.1f}" if non_errored_gpus > 0 else "N/A"
        avg_temp_str = f"{total_temp / non_errored_gpus:.1f}" if non_errored_gpus > 0 else "N/A"
        total_power_str = f"{total_power:.0f}" if valid_power_readings > 0 else "N/A"
        
        # Determine final host status emoji
        if host_status_emoji == "✅" and has_gpu_warnings:
            host_status_emoji = "⚠️"
        
        host_display_name = f"{host_status_emoji} {host}"

        busy_total_str = "" # Corrected: Initialize as empty string
        if host_status_emoji == "❌" and not any(gpu.get("error") for gpu in gpus_on_host): # Host error, not GPU error
             busy_total_str = Text("N/A", style=STYLE_ERROR)
        elif total_gpus == 0: # Should be caught by 'No GPU data' earlier
            busy_total_str = Text("0/0", style="dim")
        elif busy_gpu_count > 0:
            busy_total_str = f"🔥 {busy_gpu_count}/{total_gpus}"
        else:
            busy_total_str = f"💤 {busy_gpu_count}/{total_gpus}"

        gpu_types_str = ", ".join(sorted(list(gpu_names)))
        available_gpu_ids_str = _format_gpu_ids_to_ranges(available_gpu_ids)


        table.add_row(
            host_display_name,
            busy_total_str, # New busy/total string
            available_gpu_ids_str, # Formatted available IDs
            avg_util_str,
            avg_mem_str,
            avg_temp_str,
            total_power_str,
            Text(gpu_types_str, style=STYLE_GPU_NAME)
        )
    return table

def generate_problem_gpus_table(all_host_data: list, cluster_display_name: str) -> Table | None:
    table = Table(title=f"Problematic GPUs - {cluster_display_name}", expand=True, show_lines=True, show_edge=True, box=None)
    table.add_column("Host", style=STYLE_HOST, min_width=12)
    table.add_column("GPU ID", justify="center", min_width=4)
    table.add_column("GPU Name", style=STYLE_GPU_NAME, min_width=20)
    table.add_column("Util (%)", justify="right", min_width=8)
    table.add_column("Temp (°C)", justify="right", min_width=8)
    table.add_column("Issue / Error", justify="left", min_width=25, overflow="fold")

    problem_gpus = []
    # flat_data = [gpu for host_gpus_list in all_host_data for gpu in host_gpus_list] # This was incorrect as all_host_data is already flat

    for gpu_item in all_host_data: # Iterate directly over the already flat list
        if not isinstance(gpu_item, dict):
            # This case should ideally not happen if data collection is correct
            # Optionally, log this occurrence
            CONSOLE.print(f"[bold red]Warning: Encountered non-dictionary item in problem GPU data: {type(gpu_item)} - {str(gpu_item)[:100]}[/bold red]")
            continue

        issues = []
        if gpu_item.get("error"):
            issues.append(Text(gpu_item["error"], style=STYLE_ERROR))
        else:
            util, temp = gpu_item.get("utilization", 0), gpu_item.get("temperature", 0)
            if util >= UTILIZATION_WARN_THRESHOLD:
                style = STYLE_CRITICAL if util >= UTILIZATION_CRIT_THRESHOLD else STYLE_WARNING
                issues.append(Text(f"High Util: {util:.1f}%", style=style))
            if temp >= TEMP_WARN_THRESHOLD:
                style = STYLE_CRITICAL if temp >= TEMP_CRIT_THRESHOLD else STYLE_WARNING
                issues.append(Text(f"High Temp: {temp:.1f}°C", style=style))
        
        if issues: problem_gpus.append({**gpu_item, "issues_text": Group(*issues)})

    if not problem_gpus: return None

    sorted_problem_gpus = sorted(problem_gpus, key=_natural_sort_key_for_gpu)

    for gpu in sorted_problem_gpus:
        util_text = Text(f"{gpu.get('utilization', 0):.1f}%")
        temp_text = Text(f"{gpu.get('temperature', 0):.1f}°C")
        if gpu.get("utilization", 0) >= UTILIZATION_CRIT_THRESHOLD : util_text.stylize(STYLE_CRITICAL)
        elif gpu.get("utilization", 0) >= UTILIZATION_WARN_THRESHOLD : util_text.stylize(STYLE_WARNING)
        if gpu.get("temperature", 0) >= TEMP_CRIT_THRESHOLD : temp_text.stylize(STYLE_CRITICAL)
        elif gpu.get("temperature", 0) >= TEMP_WARN_THRESHOLD : temp_text.stylize(STYLE_WARNING)
        
        if gpu.get("error"): util_text, temp_text = Text("-", style=STYLE_ERROR), Text("-", style=STYLE_ERROR)

        table.add_row(
            Text(gpu.get("host", "N/A")), Text(str(gpu.get("gpu_id", "-")), justify="center"),
            Text(gpu.get("name", "N/A (Error)")), util_text, temp_text,
            gpu.get("issues_text", Text("Unknown Issue", style=STYLE_ERROR))
        )
    return table

def generate_detailed_gpu_table(all_host_data: list, cluster_display_name: str) -> Table:
    table = Table(title=f"All GPUs Detailed - {cluster_display_name} (Updated: {time.strftime('%Y-%m-%d %H:%M:%S')})",
                  expand=True, show_lines=True, show_edge=True, box=None)
    table.add_column("Host", style=STYLE_HOST, justify="left", min_width=12)
    table.add_column("GPU ID", justify="center", min_width=4)
    table.add_column("GPU Name", style=STYLE_GPU_NAME, justify="left", min_width=22, overflow="fold")
    table.add_column("Util (%)", justify="right", min_width=8)
    table.add_column("Temp (°C)", justify="right", min_width=8)
    table.add_column("Mem (MiB)", justify="right", min_width=12)
    table.add_column("Pwr (W)", justify="right", min_width=12)
    table.add_column("Status/Error", justify="left", min_width=25, overflow="fold")

    flat_data = [gpu for host_data_list in all_host_data for gpu in host_data_list]
    sorted_data = sorted(flat_data, key=_natural_sort_key_for_gpu)

    for gpu in sorted_data:
        host = gpu.get("host", "N/A")
        if gpu.get("error"):
            table.add_row(
                Text(host), Text(str(gpu.get("gpu_id", "-")), justify="center"),
                Text(gpu.get("name", "")), "", "", "", "",
                Text(gpu["error"], style=STYLE_ERROR)
            )
            continue

        util, temp = gpu.get("utilization", 0.0), gpu.get("temperature", 0.0)
        util_style = STYLE_OK
        if util >= UTILIZATION_CRIT_THRESHOLD: util_style = STYLE_CRITICAL
        elif util >= UTILIZATION_WARN_THRESHOLD: util_style = STYLE_WARNING
        temp_style = STYLE_OK
        if temp >= TEMP_CRIT_THRESHOLD: temp_style = STYLE_CRITICAL
        elif temp >= TEMP_WARN_THRESHOLD: temp_style = STYLE_WARNING

        mem_str = f"{gpu.get('memory_used', 0.0):.0f}/{gpu.get('memory_total', 1.0):.0f}"
        power_draw, power_limit = gpu.get("power_draw"), gpu.get("power_limit")
        power_str = "N/A"
        if power_draw is not None and power_limit is not None: power_str = f"{power_draw:.0f}/{power_limit:.0f}"
        elif power_draw is not None: power_str = f"{power_draw:.0f}/---"

        table.add_row(
            Text(host),
            Text(str(gpu.get("gpu_id", "-")), justify="center"),
            Text(gpu.get("name", "N/A"), style=STYLE_GPU_NAME),
            Text(f"{util:.1f}", style=util_style),
            Text(f"{temp:.1f}", style=temp_style),
            mem_str,
            power_str,
            Text("OK", style=STYLE_OK)
        )
    return table

def ensure_config_dir_exists(config_dir_path: str):
    """Ensures the configuration directory exists."""
    try:
        os.makedirs(config_dir_path, exist_ok=True)
    except OSError as e:
        CONSOLE.print(f"[bold red]Error: Could not create config directory {config_dir_path}: {e}[/bold red]")
        sys.exit(1)

def list_cluster_configs(config_dir):
    ensure_config_dir_exists(config_dir)
    try:
        files = [f for f in os.listdir(config_dir) if f.endswith((".yaml", ".yml"))]
        if not files:
            CONSOLE.print(f"No cluster configuration files found in '{config_dir}'.")
            CONSOLE.print(f"Use 'gpu-cluster-monitor add-cluster <new_cluster_name>' to create one.")
            return []
        CONSOLE.print(f"[bold]Available cluster configurations in '{config_dir}':[/bold]")
        for f in sorted(files): CONSOLE.print(f"  - {os.path.splitext(f)[0]}")
        return [os.path.splitext(f)[0] for f in sorted(files)]
    except FileNotFoundError: # Should be caught by ensure_config_dir_exists, but as fallback
        CONSOLE.print(f"Config directory '{config_dir}' not found (this should not happen).")
        return []

def add_cluster_interactive(config_dir: str, cluster_name: str):
    """Interactively adds a new cluster configuration."""
    ensure_config_dir_exists(config_dir)
    # Validate cluster_name to prevent path traversal or invalid filenames
    if not cluster_name or "/" in cluster_name or "\\" in cluster_name or cluster_name.startswith("."):
        CONSOLE.print(f"[bold red]Invalid cluster name: '{cluster_name}'. Cannot contain slashes or be hidden.[/bold red]")
        return

    cluster_file_path = os.path.join(config_dir, f"{cluster_name}.yaml")

    if os.path.exists(cluster_file_path):
        CONSOLE.print(f"[bold yellow]Cluster '{cluster_name}' already exists at {cluster_file_path}.[/bold yellow]")
        overwrite = input("Overwrite? [y/N]: ").strip().lower()
        if overwrite != 'y':
            CONSOLE.print("Aborted.")
            return

    CONSOLE.print(f"Adding new cluster: [bold cyan]{cluster_name}[/bold cyan]")
    display_name = input(f"Enter a display name for the cluster (or press Enter to use '{cluster_name}'): ").strip()
    if not display_name:
        display_name = cluster_name

    hosts_list = []
    CONSOLE.print("Enter hostnames for this cluster, one per line. Press Enter on an empty line to finish.")
    while True:
        host_entry = input(f"Host #{len(hosts_list) + 1}: ").strip()
        if not host_entry:
            break
        hosts_list.append(host_entry)
    
    if not hosts_list:
        CONSOLE.print("[bold red]No hosts provided. Aborting.[/bold red]")
        return

    config_data = {
        "cluster_name": display_name,
        "hosts": hosts_list
    }

    try:
        with open(cluster_file_path, 'w') as f:
            yaml.dump(config_data, f, sort_keys=False, indent=2)
        CONSOLE.print(f"[bold green]Cluster '{cluster_name}' successfully saved to {cluster_file_path}[/bold green]")
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
        CONSOLE.print(f"[bold red]Error: Cluster configuration '{cluster_name}' not found in {config_dir}.[/bold red]")
        list_cluster_configs(config_dir)
        return

    CONSOLE.print(f"About to remove cluster: [bold yellow]{cluster_name}[/bold yellow] from [cyan]{actual_file_path}[/cyan]")
    confirm = input("Are you sure you want to delete this cluster configuration? [y/N]: ").strip().lower()

    if confirm == 'y':
        try:
            os.remove(actual_file_path)
            CONSOLE.print(f"[bold green]Cluster '{cluster_name}' removed successfully.[/bold green]")
        except OSError as e:
            CONSOLE.print(f"[bold red]Error removing cluster file {actual_file_path}: {e}[/bold red]")
    else:
        CONSOLE.print("Removal aborted.")

def run_monitor(args):
    """Main logic for running the GPU cluster monitor dashboard."""
    ensure_config_dir_exists(args.config_dir)

    if not args.cluster_config_name:
        CONSOLE.print("[bold red]Error: Cluster configuration name not provided for monitoring.[/bold red]")
        list_cluster_configs(args.config_dir)
        CONSOLE.print(f"\nUsage: gpu-cluster-monitor monitor <cluster_config_name> [options]")
        return

    config_file_path = os.path.join(args.config_dir, f"{args.cluster_config_name}.yaml")
    if not os.path.exists(config_file_path):
        config_file_path_yml = os.path.join(args.config_dir, f"{args.cluster_config_name}.yml")
        if os.path.exists(config_file_path_yml):
            config_file_path = config_file_path_yml
        else:
            CONSOLE.print(f"[bold red]Error: Config file for cluster '{args.cluster_config_name}' not found in {args.config_dir}.[/bold red]")
            CONSOLE.print(f"Tried: {args.cluster_config_name}.yaml and {args.cluster_config_name}.yml")
            list_cluster_configs(args.config_dir)
            return

    cluster_cfg = load_cluster_config(config_file_path)
    if not cluster_cfg:
        return

    cluster_display_name = cluster_cfg.get("cluster_name", args.cluster_config_name)
    hosts_to_monitor = cluster_cfg.get("hosts", [])

    if not hosts_to_monitor:
        CONSOLE.print(f"[bold red]No hosts defined in config: {args.cluster_config_name}[/bold red]")
        return
    
    try: # Initial check for ssh command availability
        subprocess.run(["ssh", "-V"], capture_output=True, text=True, check=True, timeout=5)
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        CONSOLE.print("[bold red]FATAL: 'ssh' command not found or not working. Ensure OpenSSH client is installed and in PATH.[/bold red]")
        CONSOLE.print(f"Details: {e}")
        return

    results_cache = {} # Stores {'hostname': {'data': list_of_gpu_dicts, 'timestamp': datetime, 'status': str}}
    active_threads = {}  # Stores {'hostname': thread_object}

    # Pre-populate cache with initial 'loading' state for all hosts
    initial_timestamp = datetime.datetime.now()
    for host in hosts_to_monitor:
        results_cache[host] = {
            'data': [{'host': host, 'error': 'Initializing...', 'gpu_id': None}], # Ensure basic structure for table funcs
            'timestamp': initial_timestamp,
            'status': 'initializing' # 'initializing', 'updating', 'ok', 'error'
        }

    # Nested function for fetching data and updating the shared cache
    def fetch_data_for_host_and_update_cache(hostname, cli_user_override, cache):
        try:
            # Indicate that we are updating this host
            # cache[hostname]['status'] = 'updating' # Can be set here or before thread start
            # cache[hostname]['timestamp'] = datetime.datetime.now() # Update timestamp at start of fetch

            gpu_data_list = get_gpu_info_subprocess(hostname, cli_user_override)

            # Determine status based on data - assumes get_gpu_info_subprocess returns error dicts with 'error' key
            if any(isinstance(item, dict) and item.get("error") for item in gpu_data_list):
                cache[hostname]['status'] = 'error' # Could be host error or GPU specific error
            else:
                cache[hostname]['status'] = 'ok'
            cache[hostname]['data'] = gpu_data_list
            cache[hostname]['timestamp'] = datetime.datetime.now()

        except Exception as e: # Catch any unexpected error during the fetch process itself
            cache[hostname]['data'] = [{'host': hostname, 'error': f'Monitor Error: {str(e)}', 'gpu_id': None}]
            cache[hostname]['timestamp'] = datetime.datetime.now()
            cache[hostname]['status'] = 'error'
        # The thread will terminate, and is_alive() will become false.

    with Live(console=CONSOLE, refresh_per_second=1.0/args.interval, transient=False, screen=True, vertical_overflow="visible") as live:
        while True:
            # Launch/Re-launch threads for hosts that are not currently being fetched
            for host_name in hosts_to_monitor:
                if host_name not in active_threads or not active_threads[host_name].is_alive():
                    # Optionally, add logic here to only refresh if data is stale enough,
                    # e.g., if datetime.datetime.now() - results_cache[host_name]['timestamp'] > some_delta
                    results_cache[host_name]['status'] = 'updating' # Set status before starting thread
                    thread = threading.Thread(
                        target=fetch_data_for_host_and_update_cache,
                        args=(host_name, args.user, results_cache)
                    )
                    active_threads[host_name] = thread
                    thread.start()
            
            # Clean up finished threads from active_threads dict (optional, but good practice for long running app)
            finished_threads_hostnames = [hn for hn, t in active_threads.items() if not t.is_alive()]
            for hn in finished_threads_hostnames:
                del active_threads[hn]

            # Aggregate data from cache for rendering
            flat_results = []
            for host_name in hosts_to_monitor:
                # Use .get('data') to gracefully handle if a host was somehow missed in init (should not happen)
                host_cached_data = results_cache.get(host_name, {}).get('data', [])
                if isinstance(host_cached_data, list):
                    flat_results.extend(host_cached_data)
                else: # Should not occur if cache is managed correctly
                    flat_results.append({'host': host_name, 'error': 'Cache data invalid', 'gpu_id': None, 'status': 'internal_error'})

            renderables = []
            current_time_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            title_with_time = f"[bold cyan]Cluster Overview: {cluster_display_name}[/bold cyan] (Last Updated: {current_time_str})"

            host_summary_table = generate_host_summary_table(flat_results, cluster_display_name)
            renderables.append(Panel(host_summary_table, title=title_with_time, border_style="dim blue", expand=False))

            problem_gpus_table = generate_problem_gpus_table(flat_results, cluster_display_name)
            if problem_gpus_table:
                renderables.append(Panel(problem_gpus_table, title="[bold yellow]Attention: Problematic GPUs[/bold yellow]", border_style="yellow", expand=False))
            else:
                renderables.append(Panel(Text("\n  No problematic GPUs detected. All clear! 👍\n", style="green", justify="center"), 
                                         title="[bold green]GPU Status[/bold green]", border_style="dim green", expand=False))

            if args.show_all_gpus:
                detailed_table = generate_detailed_gpu_table(flat_results, cluster_display_name)
                renderables.append(Panel(detailed_table, title="[bold dim white]All GPUs (Detailed)[/bold dim white]", border_style="dim white", expand=True))
            
            # Prepend a general status/timestamp line if preferred over embedding in panel title
            # status_line = Text(f"Last Update: {current_time_str} | Refresh: {args.interval}s", style="dim white")
            # live.update(Group(status_line, *renderables))
            live.update(Group(*renderables))

def main():
    parser = argparse.ArgumentParser(
        description="GPU Cluster Monitor CLI. Manages and displays GPU stats from remote hosts.",
        formatter_class=argparse.RawTextHelpFormatter # Allows for better help text formatting
    )
    # Global argument applicable to all subcommands that might use it
    parser.add_argument(
        "--config-dir", 
        default=DEFAULT_CLUSTER_CONFIG_DIR, 
        help=f"Directory for cluster YAML files.\nDefault: {DEFAULT_CLUSTER_CONFIG_DIR}"
    )
    
    subparsers = parser.add_subparsers(title="Commands", dest="command", help="Available commands. Type a command followed by -h for more help.")
    subparsers.required = True # Make a subcommand required

    # Monitor command
    monitor_parser = subparsers.add_parser("monitor", help="Monitor a specified cluster.")
    monitor_parser.add_argument("cluster_config_name", help="Name of the cluster config file (e.g., 'my_cluster').")
    monitor_parser.add_argument("--user", help="SSH username to use. Overrides system ssh_config User for this run.")
    monitor_parser.add_argument("--interval", type=int, default=REFRESH_INTERVAL, help=f"Refresh interval (seconds). Default: {REFRESH_INTERVAL}")
    monitor_parser.add_argument("--show-all-gpus", action="store_true", help="Show the detailed table for all GPUs.")
    monitor_parser.set_defaults(func=run_monitor)

    # List clusters command
    list_parser = subparsers.add_parser("list-clusters", help="List available cluster configurations.")
    list_parser.set_defaults(func=lambda args_ns: list_cluster_configs(args_ns.config_dir))

    # Add cluster command
    add_parser = subparsers.add_parser("add-cluster", help="Add a new cluster configuration interactively.")
    add_parser.add_argument("cluster_name", help="Name for the new cluster (e.g., 'my_cluster'). This will be the filename.")
    add_parser.set_defaults(func=lambda args_ns: add_cluster_interactive(args_ns.config_dir, args_ns.cluster_name))

    # Remove cluster command
    remove_parser = subparsers.add_parser("remove-cluster", help="Remove an existing cluster configuration.")
    remove_parser.add_argument("cluster_name", help="Name of the cluster to remove.")
    remove_parser.set_defaults(func=lambda args_ns: remove_cluster_interactive(args_ns.config_dir, args_ns.cluster_name))

    args = parser.parse_args()

    # Ensure the main config directory (e.g. ~/.gpu-cluster-monitor) exists before any command that might need it.
    # Specific functions like add_cluster_interactive also call this, but it's good for robustness.
    if hasattr(args, 'config_dir'): # All our commands should have it due to global arg or specific add.
        ensure_config_dir_exists(args.config_dir)

    if hasattr(args, 'func'):
        args.func(args)
    else:
        # This should not be reached if subparsers.required = True and all subparsers have set_defaults(func=...)
        parser.print_help()
        sys.exit(1)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        CONSOLE.print("\n[bold green]Exiting GPU Dashboard. Goodbye![/bold green]")
    except Exception: 
        CONSOLE.print_exception(show_locals=False)

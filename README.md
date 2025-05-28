# GPU Cluster Monitor

A CLI dashboard to monitor GPU utilization, temperature, memory, and power usage on remote hosts via SSH. It provides a live-updating table view, summarizing GPU status across multiple machines defined in a cluster configuration file.

## Features

-   Live monitoring of multiple GPUs across multiple hosts.
-   Color-coded thresholds for critical and warning states (utilization, temperature).
-   Displays GPU ID, name, utilization, memory (used/total), temperature, and power draw/limit.
-   Supports SSH connection via system `ssh` command, leveraging `~/.ssh/config` for host specifics (including `ProxyCommand`).
-   Configurable refresh interval.
-   Host summary table for a quick overview.
-   Problematic GPUs table highlighting GPUs with errors or high utilization/temperature.
-   Optional detailed table for all GPUs.
-   Natural sorting for hostnames (e.g., h1, h2, h10).

## Prerequisites

-   Python 3.10+
-   OpenSSH client installed and configured (i.e., `ssh` command works and can connect to target hosts, potentially using `~/.ssh/config`).
-   `nvidia-smi` installed on all remote GPU hosts.

## Installation

It is highly recommended to install `gpu-cluster-monitor` in a virtual environment.

1.  **Create and activate a virtual environment (recommended):**
    ```bash
    python -m venv .venv
    source .venv/bin/activate  # On Windows use `.venv\Scripts\activate`
    ```

2.  **Install `gpu-cluster-monitor` from PyPI:**
    ```bash
    pip install gpu-cluster-monitor
    ```

## Configuration

`gpu-cluster-monitor` stores its cluster configuration files by default in `~/.config/gpu-cluster-monitor/clusters/` (on Linux/macOS) or the appropriate user config directory for your OS. You can manage these configurations using the `gpu-cluster-monitor add-cluster` and `gpu-cluster-monitor list-clusters` commands.

Alternatively, you can specify a custom configuration directory using the `--config-dir` option with any command.

Cluster configuration files are in YAML format. Here's an example (`my_cluster.yaml`):

```yaml
cluster_name: "My Awesome GPU Cluster"
hosts:
  - server1.example.com
  - server2
  - gpu-node-01
  - user@gpu-node-02 # You can specify user@host if needed
```

-   `cluster_name`: A display name for your dashboard.
-   `hosts`: A list of hostnames or IP addresses of the machines to monitor. Your `~/.ssh/config` will be used for connection details like username, port, identity files, proxy commands, etc.

Use the `gpu-cluster-monitor add-cluster <cluster_name>` command to interactively create a new configuration file.

## Usage

After installation, you can run the monitor using the `gpu-cluster-monitor` command.

**Commands:**

*   `gpu-cluster-monitor monitor <cluster_name> [options]`
    *   Monitors the specified cluster.
    *   `--user USERNAME`: SSH username (overrides `~/.ssh/config` or system defaults).
    *   `--interval SECONDS`: Refresh interval (default: 2 seconds).
    *   `--show-all-gpus`: Show detailed GPU table in addition to summaries.
    *   `--config-dir DIRECTORY`: Override default config directory (`~/.config/gpu-cluster-monitor/clusters/`).

*   `gpu-cluster-monitor list-clusters [--config-dir DIRECTORY]`
    *   Lists available cluster configurations from the config directory.

*   `gpu-cluster-monitor add-cluster <new_cluster_name> [--config-dir DIRECTORY]`
    *   Interactively helps you create a new cluster configuration file.

*   `gpu-cluster-monitor remove-cluster <cluster_to_remove> [--config-dir DIRECTORY]`
    *   Removes an existing cluster configuration file after confirmation.

**Example: Monitoring a cluster**

1.  Add a cluster configuration if you haven't already:
    ```bash
    gpu-cluster-monitor add-cluster my_servers
    ```
    (Follow the prompts to add hostnames)

2.  Monitor the cluster:
    ```bash
    gpu-cluster-monitor monitor my_servers
    ```

**To list available clusters:**

```bash
gpu-cluster-monitor list-clusters
```

## Troubleshooting

*   **Permission Denied:** Ensure your SSH keys are set up correctly, your SSH agent is running with the right keys, or your `~/.ssh/config` has the correct `User` and `IdentityFile` for the target hosts.
*   **Could not resolve hostname:** Check that the hostname is correct and resolvable from the machine running the monitor.
*   **Connection timed out:** Verify network connectivity to the host and that the SSH port (usually 22) is open. Check `ProxyCommand` settings in `~/.ssh/config` if you use a bastion/jump host.
*   **`nvidia-smi` not found on host:** Ensure `nvidia-smi` is installed and in the `PATH` for the SSH user on the remote machine.
*   **`'ssh' command not found locally`:** Make sure the OpenSSH client is installed on the machine where you are running `gpu-cluster-monitor`.
*   **Configuration file issues:** If you suspect a problem with a config file, you can try removing it (e.g., `gpu-cluster-monitor remove-cluster <name>`) and adding it again.

## Contributing & Development

Contributions are welcome! Please feel free to submit a Pull Request or open an Issue.

### Setting up for Development

1.  **Clone the repository:**
    ```bash
    git clone https://github.com/AgrawalAmey/gpu-cluster-monitor.git
    cd gpu-cluster-monitor
    ```

2.  **Create and activate a virtual environment (recommended):**
    ```bash
    python -m venv .venv
    source .venv/bin/activate  # On Windows use `.venv\Scripts\activate`
    ```

3.  **Install the package in editable mode with development dependencies:**
    The `Makefile` simplifies this. Ensure you have `make` installed.
    ```bash
    make install 
    ```
    This target typically installs the package in editable mode (`pip install -e .`) and also installs tools like `build` and `twine`.

    If you don't have `make` or prefer manual steps:
    ```bash
    pip install -e ".[dev]" 
    ```
    (Ensure `pyproject.toml` has a `[project.optional-dependencies]` table for `dev` if using this manual command).

### Running from Source (for development)

If you have cloned the repository and installed dependencies in editable mode, you can invoke the CLI directly:
```bash
gpu-cluster-monitor --help
```

Alternatively, to run the module directly without relying on the entry point (useful for some debugging scenarios):
```bash
python -m gpu_cluster_monitor.main monitor <cluster_config_name> [options]
# Example for adding a cluster using local config files:
# python -m gpu_cluster_monitor.main add-cluster dev_cluster --config-dir ./clusters_config
```
Note: When running with `python -m`, if you want to use local `clusters_config` files from the project root for testing, you'll need to specify `--config-dir ./clusters_config` as the default will still be `~/.config/gpu-cluster-monitor/clusters/`.

### Makefile for Development

A `Makefile` is provided to simplify common development tasks.

**Common Makefile Targets:**

*   `make venv`: Creates a Python virtual environment in `.venv/`.
*   `make install`: Installs the package in editable mode and development dependencies. Assumes virtual environment is active.
*   `make build`: Builds the package (sdist and wheel) into the `dist/` directory.
*   `make clean`: Removes build artifacts and `__pycache__` directories.
*   `make publish_test`: Uploads the package to TestPyPI from the `dist/` directory.
*   `make publish`: Uploads the package to PyPI from the `dist/` directory.
*   `make lint`: (Placeholder) Should be configured to run linters/formatters like Black, Flake8, or Ruff.

**Typical Development Workflow:**

1.  `make venv` (first time, or if `.venv` is deleted)
2.  `source .venv/bin/activate` (or your shell's equivalent)
3.  `make install` (to set up editable install and dev tools)
4.  (Make your code changes)
5.  (Optionally, run `make lint` or other checks)
6.  `make build`
7.  `make publish_test` (to test packaging and upload to TestPyPI)
8.  `make publish` (to release to PyPI)

## License

This project is licensed under the Apache License 2.0 - see the `LICENSE` file for details.
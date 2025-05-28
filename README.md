# GPU Cluster Monitor

A CLI dashboard to monitor GPU utilization, temperature, memory, and power usage on remote hosts via SSH. It provides a live-updating table view, summarizing GPU status across multiple machines defined in a cluster configuration file.

## Features

-   Live monitoring of multiple GPUs across multiple hosts.
-   Color-coded thresholds for critical and warning states (utilization, temperature).
-   Displays GPU ID, name, utilization, memory (used/total), temperature, and power draw/limit.
-   Supports SSH connection via system `ssh` command, leveraging `~/.ssh/config` for host specifics (including `ProxyCommand`).
-   Configurable refresh interval.
-   Host summary table for a quick overview.
-   Problematic GPUs table highlighting GPUs with errors or high temperatures.
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
    **Important**: Ensure the virtual environment is activated in your shell *before* proceeding to the next steps involving `make` or `pip install -e`.

2.  **Install `gpu-cluster-monitor` from PyPI:**
    ```bash
    pip install gpu-cluster-monitor
    ```

## Configuration

`gpu-cluster-monitor` uses two main configuration files, typically located in `~/.gpu-cluster-monitor/` (this path is created if it doesn't exist upon first run or when initializing settings):

1.  **`clusters.yaml`**: Defines the clusters and hosts to monitor.
2.  **`settings.yaml`**: Configures application behavior like refresh intervals, display emojis, and warning thresholds.

### 1. Cluster Configuration (`clusters.yaml`)

You need to manually create or edit `~/.gpu-cluster-monitor/clusters.yaml`. Here's an example structure:

```yaml
clusters:
  - name: "my_main_cluster"  # Internal name, used for selecting with `gpu-monitor monitor <name>`
    display_name: "My Awesome GPU Cluster" # Display name for the dashboard
    ssh_user: "default_user_for_cluster" # Optional: Default SSH user for all hosts in this cluster
    ssh_key: "~/.ssh/id_rsa_cluster"    # Optional: Default SSH key for all hosts in this cluster
    ssh_port: 2222                     # Optional: Default SSH port
    hosts:
      - server1.example.com
      - server2
      - name: gpu-node-01 # Can be a simple string or a dict for host-specific overrides
        ssh_user: "specific_user" # Host-specific SSH user
      - name: gpu-node-02
        ssh_key: "~/.ssh/id_rsa_node02"
      # Add more hosts as needed
  
  - name: "another_cluster"
    display_name: "Secondary GPU Farm"
    hosts:
      - worker1
      - worker2
```

-   **`clusters`**: A list of cluster definitions.
    -   **`name`**: An internal identifier for the cluster. If you want to monitor only this specific cluster, you'll use this name with the `monitor` command (e.g., `gpu-monitor monitor my_main_cluster`).
    -   **`display_name`**: A user-friendly name shown in the dashboard title for this cluster.
    -   **`ssh_user` (optional)**: Default SSH username for all hosts in this cluster. Can be overridden per host.
    -   **`ssh_key` (optional)**: Path to the default SSH private key for this cluster. Can be overridden per host.
    -   **`ssh_port` (optional)**: Default SSH port for this cluster. Defaults to 22 if not specified. Can be overridden per host.
    -   **`hosts`**: A list of host entries. Each entry can be:
        -   A simple string (hostname or IP).
        -   A dictionary with a `name` key (hostname or IP) and optional `ssh_user`, `ssh_key`, `ssh_port` to override cluster defaults or system SSH config for that specific host.

Your system's `~/.ssh/config` will still be respected for connection details if not specified in `clusters.yaml` (e.g., for `ProxyCommand`, `User` if not set in `clusters.yaml`, `IdentityFile` if not set).

### 2. Application Settings (`settings.yaml`)

This file controls various aspects of the monitor's appearance and behavior. To create a default `settings.yaml` file, run:

```bash
gpu-monitor settings init
```

This will create `~/.gpu-cluster-monitor/settings.yaml` with default values. You can then edit this file to customize:
-   Refresh intervals
-   SSH and `nvidia-smi` command timeouts
-   Utilization and temperature thresholds for warnings and critical alerts
-   Emojis used for status indicators

If `settings.yaml` is not present, the application will use built-in default values.

## Usage

After installation and configuration, you can run the monitor using the `gpu-monitor` command.

**Commands:**

*   `gpu-monitor monitor [cluster_name] [options]`
    *   Monitors the specified cluster by its `name` from `clusters.yaml`. 
    *   If `cluster_name` is omitted, all clusters defined in `clusters.yaml` are monitored.
    *   `--interval SECONDS`: Refresh interval (overrides `settings.yaml` or default).
    *   `--show-all-gpus`: Show detailed GPU table in addition to summaries.
    *   `--config PATH_TO_CLUSTERS.YAML`: Path to the cluster configuration YAML file. Default: `~/.gpu-cluster-monitor/clusters.yaml`.
    *   `--config-dir DIRECTORY`: Path to the configuration directory where `clusters.yaml` and `settings.yaml` are located. Default: `~/.gpu-cluster-monitor`.
    *   `--ssh-debug`: Enable detailed SSH command debugging output.

*   `gpu-monitor settings init`
    *   Creates a default `settings.yaml` file in the configuration directory (`~/.gpu-cluster-monitor/settings.yaml`) if one doesn't already exist.

**Example: Monitoring clusters**

1.  Ensure `~/.gpu-cluster-monitor/clusters.yaml` is configured with your cluster(s).
2.  (Optional) Initialize and customize `settings.yaml`:
    ```bash
    gpu-monitor settings init
    # Now edit ~/.gpu-cluster-monitor/settings.yaml if desired
    ```
3.  Monitor all clusters defined in `clusters.yaml`:
    ```bash
    gpu-monitor monitor
    ```
4.  Monitor a specific cluster named `my_main_cluster` (assuming it's defined in `clusters.yaml`):
    ```bash
    gpu-monitor monitor my_main_cluster
    ```

## Troubleshooting

*   **Permission Denied:** Ensure your SSH keys are set up correctly, your SSH agent is running with the right keys, or your `~/.ssh/config` has the correct `User` and `IdentityFile` for the target hosts. Host-specific or cluster-specific `ssh_user` and `ssh_key` in `clusters.yaml` can also be used.
*   **Could not resolve hostname:** Check that the hostname is correct and resolvable from the machine running the monitor.
*   **Connection timed out:** Verify network connectivity to the host and that the SSH port (usually 22, unless overridden) is open. Check `ProxyCommand` settings in `~/.ssh/config` if you use a bastion/jump host.
*   **`nvidia-smi` not found on host:** Ensure `nvidia-smi` is installed and in the `PATH` for the SSH user on the remote machine.
*   **`'ssh' command not found locally`:** Make sure the OpenSSH client is installed on the machine where you are running `gpu-monitor`.
*   **YAML parsing errors**: Carefully check the syntax of your `clusters.yaml` or `settings.yaml` files. Online YAML validators can be helpful.

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
    **Important**: Ensure the virtual environment is activated in your shell *before* proceeding to the next steps involving `make` or `pip install -e`.

3.  **Install the package in editable mode with development dependencies:**
    The `Makefile` simplifies this. Ensure you have `make` installed and your virtual environment is active.
    ```bash
    make install 
    ```
    This target installs the package in editable mode and development tools like `build` and `twine`.
    All subsequent `make` targets (like `build`, `lint`, `publish`) also assume the virtual environment is active.

    If you don't have `make` or prefer manual steps (ensure venv is active):
    ```bash
    pip install -e ".[dev]" # Installs in editable mode with dev dependencies
    pip install --upgrade build twine # Installs packaging tools
    ```
    (Ensure `pyproject.toml` has a `[project.optional-dependencies]` table for `dev` if using the `.[dev]` syntax).

### Running from Source (for development)

If you have cloned the repository, activated your virtual environment, and installed dependencies in editable mode, you can invoke the CLI directly:
```bash
gpu-monitor --help
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
**Important**: Before running targets like `install`, `build`, `lint`, `publish_test`, or `publish`, ensure you have activated your virtual environment (e.g., `source .venv/bin/activate`). The `make venv` target only *creates* the environment.

**Common Makefile Targets:**

*   `make venv`: Creates a Python virtual environment in `.venv/`.
*   `make install`: Installs the package in editable mode and development dependencies. **Assumes virtual environment is active.**
*   `make build`: Builds the package (sdist and wheel) into the `dist/` directory. **Assumes virtual environment is active.**
*   `make clean`: Removes build artifacts and `__pycache__` directories.
*   `make publish_test`: Uploads the package to TestPyPI from the `dist/` directory. **Assumes virtual environment is active.**
*   `make publish`: Uploads the package to PyPI from the `dist/` directory. **Assumes virtual environment is active.**
*   `make lint`: Runs linters and formatters (e.g., Ruff). **Assumes virtual environment is active.**
*   `make format`: Runs formatters (e.g., Ruff). **Assumes virtual environment is active.**

**Typical Development Workflow:**

1.  `make venv` (first time, or if `.venv` is deleted)
2.  `source .venv/bin/activate` (or your shell's equivalent) - **Crucial step!**
3.  `make install` (to set up editable install and dev tools)
4.  (Make your code changes)
5.  (Optionally, run `make lint` or `make format`)
6.  `make build`
7.  `make publish_test` (to test packaging and upload to TestPyPI)
8.  `make publish` (to release to PyPI)

## License

This project is licensed under the Apache License 2.0 - see the `LICENSE` file for details.
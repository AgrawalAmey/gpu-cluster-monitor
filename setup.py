import os
from typing import List
from setuptools import setup, find_packages
from setuptools_scm import get_version


ROOT_DIR = os.path.dirname(__file__)


def get_requirements() -> List[str]:
    """Get Python package dependencies from requirements.txt."""
    with open("requirements.txt") as f:
        requirements = f.read().strip().split("\n")
    return requirements


setup(
    author="Amey Agrawal",
    author_email="agrawalamey12@gmail.com",
    python_requires=">=3.10",
    description="A CLI dashboard to monitor GPU utilization and other metrics on remote hosts via SSH.",
    keywords="GPU, Monitor, Dashboard, CLI, SSH, NVIDIA, NVIDIA-SMI, Rich",
    name="gpu-cluster-monitor",
    packages=find_packages(include=["gpu_cluster_monitor", "gpu_cluster_monitor.*"]),
    long_description=open("README.md").read(),
    long_description_content_type="text/markdown",
    url="https://github.com/AgrawalAmey/gpu-cluster-monitor",
    version=get_version(),
    install_requires=get_requirements(),
    entry_points={
        "console_scripts": [
            "gpu-cluster-monitor = gpu_cluster_monitor.main:main",
        ],
    },
    classifiers=[
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "License :: OSI Approved :: MIT License",
        "Topic :: System :: Monitoring",
    ],
)

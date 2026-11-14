"""Worker agent for Campus Edge Fabric.

The agent is intentionally dumb about scheduling: it asks the coordinator for as
many compatible tasks as it can run right now, executes them locally with a
pre-approved workload adapter, and reports back. It never accepts arbitrary code
or shell commands from the network.
"""

__version__ = "0.1.0"

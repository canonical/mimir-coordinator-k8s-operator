from typing import Optional, Callable, Union
import re
import yaml
import logging
import pathlib
from ops.pebble import PathError, APIError
from dataclasses import dataclass
from ops.model import BlockedStatus, WaitingStatus
from yaml.parser import ParserError

logger = logging.getLogger(__name__)


@dataclass
class Status:
    """'Dumb struct' for helping with centralized status setting."""

    # None = good; do not use ActiveStatus here.
    update_config: Optional[Union[BlockedStatus, WaitingStatus]] = None


class WorkloadManager:
    CONFIG_PATH = "/etc/grafana-agent.yaml"

    def __init__(self, charm, container_name: str, config_getter: Callable[[], ...]):
        # Property to facilitate centralized status update
        self.status = Status()

        self._unit = charm.unit

        self._service_name = self._container_name = container_name
        self._container = charm.unit.get_container(container_name)

        self._render_config = config_getter

        # turn the container name to a valid Python identifier
        snake_case_container_name = self._container_name.replace("-", "_")
        charm.framework.observe(
            getattr(charm.on, "{}_pebble_ready".format(snake_case_container_name)),
            self._on_pebble_ready,
        )

    def _cli_args(self) -> str:
        """Return the cli arguments to pass to agent.

        Returns:
            The arguments as a string
        """
        return f"-config.file={self.CONFIG_PATH}"

    def _on_pebble_ready(self):
        self.write_file(self.CONFIG_PATH, yaml.dump(self._render_config()))

        pebble_layer = {
            "summary": "agent layer",
            "description": "pebble config layer for Grafana Agent",
            "services": {
                "agent": {
                    "override": "replace",
                    "summary": "agent",
                    "command": f"/bin/agent {self._cli_args()}",
                    "startup": "enabled",
                },
            },
        }
        self._container.add_layer(self._service_name, pebble_layer, combine=True)
        self._container.autostart()

        if version := self.version:
            self._unit.set_workload_version(version)
        else:
            logger.debug(
                "Cannot set workload version at this time: could not get grafana-agent version."
            )

        # self._update_status()

    def is_ready(self):
        return self._container.can_connect()

    @property
    def version(self) -> Optional[str]:
        """Returns the version of the agent.

        Returns:
            A string equal to the agent version
        """
        if not self.is_ready:
            return None

        # Output looks like this:
        # agent, version v0.26.1 (branch: HEAD, revision: 2b88be37)
        version_output, _ = self._container.exec(["/bin/agent", "-version"]).wait_output()
        result = re.search(r"v(\d*\.\d*\.\d*)", version_output)
        return result.group(1) if result else None

    def read_file(self, filepath: Union[str, pathlib.Path]):
        """Read a file's contents.

        Returns:
            A string with the file's contents
        """
        return self._container.pull(filepath).read()

    def write_file(self, path: Union[str, pathlib.Path], text: str) -> None:
        """Write text to a file.

        Args:
            path: file path to write to
            text: text to write to the file
        """
        self._container.push(path, text, make_dirs=True)

    def restart(self) -> None:
        """Restart grafana agent."""
        self._container.restart(self._service_name)

    def _update_config(self) -> None:
        if not self.is_ready:
            # Workload is not yet available so no need to update config
            self.status.update_config = WaitingStatus("Workload is not yet available")
            return

        config = self._render_config()  # TODO: Must not be None
        assert config is not None

        try:
            old_config = yaml.safe_load(self.read_file(self.CONFIG_PATH))
        except (FileNotFoundError, PathError, ParserError):
            # The file does not yet exist?
            old_config = None

        if config == old_config:
            # Nothing changed, possibly new installation. Move on.
            self.status.update_config = None
            return

        try:
            self.write_file(self.CONFIG_PATH, yaml.dump(config))
            self.restart()  # to pick up the new config
        except APIError as e:
            logger.warning(str(e))
            self.status.update_config = WaitingStatus(str(e))

        self.status.update_config = None

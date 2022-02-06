import argparse
import datetime
import logging
from contextlib import contextmanager
from functools import cached_property
from typing import TextIO

from paramiko import SFTPClient, SSHClient, SSHException
from tqdm import tqdm


HASS_REMOTE_MANAGEMENT_NAME = "hass_remote_mgmt"

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
logger.addHandler(logging.StreamHandler())


class SSHExecException(Exception):
    pass


class HASSRemoteManagement:

    LOG_FILE_BASE_NAME: str = HASS_REMOTE_MANAGEMENT_NAME

    def __init__(self, hostname: str, username: str) -> None:
        self._hostname: str = hostname
        self._username: str = username
        self._progress_bar = _None

    def __del__(self):
        logger.debug("Closing SSH Client")
        if self._ssh_client:
            self._ssh_client.close()

    @cached_property
    def _now_iso8601(self) -> str:
        return datetime.datetime.now().isoformat()

    @cached_property
    def _log_file_name(self) -> str:
        return f"{self.LOG_FILE_BASE_NAME}.{self._now_iso8601}.log"

    @contextmanager
    def _log_file(self):
        f: TextIO = open(self._log_file_name, "w+")
        try:
            yield f
        finally:
            f.close()

    @cached_property
    def _ssh_client(self) -> SSHClient:
        logger.debug("Creating SSH Client")
        ssh_client = SSHClient()
        ssh_client.load_system_host_keys()
        ssh_client.connect(self._hostname, username=self._username)
        return ssh_client

    def _ssh_exec(self, command: str, max_allowed_return_code: int = 0) -> None:
        logger.info(f"Executing {command} command")
        try:
            _, stdout, _ = self._ssh_client.exec_command(command, get_pty=True)
        except SSHException as e:
            logger.exception(e)
            raise e
        with self._log_file() as f:
            lines_count = 0
            for line in stdout:
                clean_line = line.rstrip("\n")
                f.write(clean_line)
                print(f"Executing command {command}: {lines_count}", end="\r")
                lines_count += 1
        if stdout.channel.recv_exit_status() > max_allowed_return_code:
            error_msg = f"Executed returned {command} returned {stdout.channel.recv_exit_status()}"
            logger.exception(error_msg)
            raise SSHExecException(error_msg)
        logger.info("Command {} Executed".format(command))

    def _scp_loading_bar(self, current: int, total: int):
        if not self._progress_bar:
            self._progress_bar = tqdm(total=total)
        if getattr(self, "_progress_bar_last", None):
            offset = current - self._progress_bar_last
        else:
            offset = current
        self._progress_bar.update(offset)
        self._progress_bar_last = current

    def _scp(self, src: str, dest: str) -> None:
        logger.info('Copying "{}" file'.format(src))
        sftp_client: SFTPClient = self._ssh_client.open_sftp()
        sftp_client.get(src, dest, callback=self._scp_loading_bar)
        self._progress_bar = None  # TODO remove from here and clean automatically

    def backup(self) -> None:
        logger.info("Entering in backup mode")
        self._ssh_exec(
            f"sudo tar cvf /tmp/homeassistant-{self._now_iso8601}.back.tar /home/homeassistant/.homeassistant/",
            max_allowed_return_code=1,
        )
        self._ssh_exec(f"sudo bzip2 /tmp/homeassistant-{self._now_iso8601}.back.tar")
        self._scp(
            f"/tmp/homeassistant-{self._now_iso8601}.back.tar",
            f"homeassistant-{self._now_iso8601}.back.tar.bz2",
        )
        self._ssh_exec(f"sudo rm /tmp/homeassistant-{self._now_iso8601}.back.*")

    def deploy(self) -> None:
        logger.info("Starting deployment...")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    mutually_exclusive = parser.add_mutually_exclusive_group(required=True)
    mutually_exclusive.add_argument(
        "-d",
        "--deploy",
        action="store_true",
        default=False,
    )
    mutually_exclusive.add_argument(
        "-b",
        "--backup",
        action="store_true",
        default=False,
    )
    parser.add_argument("host", type=str)
    parser.add_argument("-v", "--verbose", action="store_true", default=False)
    args = parser.parse_args()

    if args.verbose:
        logger.setLevel(logging.DEBUG)

    username, host = args.host.split("@")
    manager = HASSRemoteManagement(host, username)

    fn = None
    if "backup" in args:
        fn = manager.backup
    elif "deploy" in args:
        fn = manager.deploy

    fn()

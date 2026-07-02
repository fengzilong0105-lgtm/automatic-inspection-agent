from agent.executor.ssh import wrap_command_for_host
from agent.models import SSHConfig


def test_wrap_command_without_elevation():
    ssh = SSHConfig(host="10.0.0.1", user="root", password="secret")
    assert wrap_command_for_host("whoami", ssh) == "whoami"


def test_wrap_command_with_sudo_su_and_password():
    ssh = SSHConfig(
        host="192.168.1.10",
        user="deploy",
        password="ssh-password",
        use_sudo_su=True,
    )
    wrapped = wrap_command_for_host("whoami", ssh)
    assert wrapped.startswith("printf '%s\\n' 'ssh-password' | sudo -S su - root -c ")
    assert wrapped.endswith("'whoami'")


def test_wrap_command_with_separate_sudo_password():
    ssh = SSHConfig(
        host="192.168.1.10",
        user="deploy",
        password="ssh-pass",
        use_sudo_su=True,
        sudo_password="sudo-pass",
    )
    wrapped = wrap_command_for_host("id -u", ssh)
    assert "'sudo-pass'" in wrapped
    assert "'id -u'" in wrapped

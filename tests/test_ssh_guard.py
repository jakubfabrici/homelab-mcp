from homelab_mcp.clients.ssh import looks_destructive


def test_detects_rm_rf_root():
    assert looks_destructive("rm -rf /")
    assert looks_destructive("sudo rm -rf  / ")
    assert looks_destructive("rm -fr /")


def test_detects_mkfs_and_dd():
    assert looks_destructive("mkfs.ext4 /dev/sda1")
    assert looks_destructive("dd if=/dev/zero of=/dev/sda bs=1M")


def test_allows_normal_commands():
    assert looks_destructive("rm -rf /tmp/build") is None
    assert looks_destructive("docker ps -a") is None
    assert looks_destructive("systemctl restart nginx") is None
    assert looks_destructive("ls -la /") is None

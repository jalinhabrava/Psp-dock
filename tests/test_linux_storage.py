from pathlib import Path
import tempfile
import unittest
import sys

from psp_dock.core.linux_storage import fixed_local_directory
from psp_dock.core.storage_layout import fixed_local_folder


@unittest.skipUnless(sys.platform.startswith('linux'), 'Linux adapter requires a Linux host')
class LinuxStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.sysfs = self.root / 'sys'
        self.disk = self.sysfs / 'devices/pci/nvme/nvme0n1'
        self.disk.mkdir(parents=True)
        (self.disk / 'removable').write_text('0')
        self.partition = self.disk / 'nvme0n1p1'
        self.partition.mkdir()
        (self.sysfs / 'dev/block').mkdir(parents=True)
        (self.sysfs / 'dev/block/259:1').symlink_to(self.partition, target_is_directory=True)
        self.mounts = self.root / 'mountinfo'
        self.mounts.write_text('30 1 259:1 / / rw - ext4 /dev/nvme0n1p1 rw\n')
        self.target = self.root / 'data/library'

    def check(self):
        return fixed_local_directory(self.target, sysfs_root=self.sysfs, mountinfo=self.mounts)

    def test_fixed_internal_partition_can_contain_new_library(self):
        self.assertEqual(self.check(), self.target)

    def test_usb_removable_network_and_readonly_destinations_are_rejected(self):
        (self.disk / 'idVendor').write_text('054c')
        with self.assertRaises(ValueError):
            self.check()
        (self.disk / 'idVendor').unlink()
        (self.disk / 'removable').write_text('1')
        with self.assertRaises(ValueError):
            self.check()
        (self.disk / 'removable').write_text('0')
        for filesystem, opts in [('cifs', 'rw'), ('nfs4', 'rw'), ('ext4', 'ro'), ('vfat', 'rw'), ('overlay', 'rw')]:
            self.mounts.write_text(f'30 1 259:1 / / {opts} - {filesystem} /dev/nvme0n1p1 {opts}\n')
            with self.subTest(filesystem=filesystem, opts=opts), self.assertRaises(ValueError):
                self.check()

    def test_luks_lvm_slave_chain_must_end_in_internal_disk(self):
        mapped = self.sysfs / 'devices/virtual/block/dm-0'
        (mapped / 'slaves').mkdir(parents=True)
        (mapped / 'removable').write_text('0')
        (mapped / 'slaves/nvme0n1p1').symlink_to(self.partition, target_is_directory=True)
        (self.sysfs / 'dev/block/253:0').symlink_to(mapped, target_is_directory=True)
        self.mounts.write_text('30 1 253:0 / / rw - ext4 /dev/mapper/home rw\n')
        self.assertEqual(self.check(), self.target)
        (self.disk / 'idVendor').write_text('1234')
        with self.assertRaises(ValueError):
            self.check()

    def test_symlink_is_rejected_before_resolving(self):
        original = self.root / 'original'
        original.mkdir()
        link = self.root / 'linked'
        link.symlink_to(original, target_is_directory=True)
        self.target = link / 'new-library'
        with self.assertRaises(ValueError):
            self.check()
        with self.assertRaises(ValueError):
            fixed_local_folder(self.target)

    def test_nested_removable_mount_overrides_internal_parent(self):
        self.target.parent.mkdir()
        self.mounts.write_text(self.mounts.read_text() + f'31 30 259:1 / {self.target.parent} rw - vfat /dev/removable rw\n')
        with self.assertRaises(ValueError):
            self.check()

    def test_all_btrfs_members_must_be_fixed_and_internal(self):
        self.mounts.write_text('30 1 259:1 / / rw - btrfs /dev/nvme0n1p1 rw\n')
        members = self.sysfs / 'fs/btrfs/synthetic-fsid/devices'
        members.mkdir(parents=True)
        (members / 'nvme0n1p1').symlink_to(self.partition, target_is_directory=True)
        self.assertEqual(self.check(), self.target)
        removable = self.sysfs / 'devices/usb2/2-1/block/sdz'
        removable.mkdir(parents=True)
        (removable / 'removable').write_text('1')
        (members / 'sdz').symlink_to(removable, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.check()

    def test_bind_mount_aliases_cannot_bypass_directory_overlap_guards(self):
        self.target.parent.mkdir()
        for extra in (
                f'31 30 259:1 /other {self.target.parent} rw - ext4 /dev/nvme0n1p1 rw\n',
                '31 30 259:1 / /alias rw - ext4 /dev/nvme0n1p1 rw\n'):
            self.mounts.write_text('30 1 259:1 / / rw - ext4 /dev/nvme0n1p1 rw\n' + extra)
            with self.assertRaisesRegex(ValueError, 'bind'):
                self.check()


if __name__ == '__main__':
    unittest.main()

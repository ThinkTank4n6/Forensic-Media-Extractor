# Fixtures taken in v2026.11.315 and v2026.11.316

Copied unchanged from the other tool in this space (GLEAPP 2026.5.7,
`tests/fixtures/`, MIT, Copyright (c) 2026 charpy4n6, Alexis Brignoni). Its
own record of where each came from is quoted; the answer files were made
outside this program, when each image was made or by the operating system
that wrote it. Used by `tests/test_real_evidence_fixtures.py`, `tests/test_raid_sets.py` and
`tests/test_acquisition_keys.py`.

| file | what it is | the answer it is checked against |
| --- | --- | --- |
| `pyaff4-zlib.aff4` | AFF4 acquisition (ewfprobe's fixtures) | the disk's size, 63,894 bytes |
| `qcow2-v3.qcow2.gz`, `vmdk-base.vmdk.gz`, `dirty-log.vhdx.gz` | one ext4 disk in three VM formats (ewfprobe's fixtures) | the three agree file for file |
| `lvm-fixture.img.gz`, `lvm-fixture.sha256` | LVM2 volume group: linear, split, striped, mirrored, grown | SHA-256 of each file, recorded when made |
| `raid/md-r1-090-0.img.gz`, `raid/md-r1-0.img.gz`, `raid/raid-trees.sha256` | members of Linux md RAID1 arrays (0.90 and 1.2 metadata) | SHA-256 of each file as the kernel presented it |
| `btrfs-raid1-dev2.img.gz` | one device of a two-device Btrfs RAID1 (qnxprobe's fixtures) | the reader's reason for listing nothing |
| `refs-fixture.bin.gz`, `refs-fixture.known.tsv.gz`, `refs-dirty.bin.gz` | ReFS 3.14 written by Windows 11, sparse (qnxprobe v1.70) | what Windows reported about all 2,544 files, SHA-256 included |
| `raid/md-r1-1.img.gz`, `raid/md-r5-ls-0..3.img.gz`, `raid/lvm-raid-multi-0..4.img.gz` | md RAID1 and RAID5 member sets; LVM RAID5/6/10 across five images | `raid/raid-trees.sha256` |
| `aff-enc-pass.aff`, `aff-enc-inplace-cert.aff`, `aff-enc-test-key.pem` | AFF encrypted with a passphrase (`ewfprobe-aff-password`) and sealed to a certificate (ewfprobe's fixtures) | the six pictures on the disk |
| `ftk-ad-cert-ad1.ad1`, `ad-cert-test-key-2048.pem` | AD1 written by FTK Imager 4.7.3.61, encrypted to a certificate | the MD5 FTK Imager stored for each entry |
| `decmpfs-apfs.img.gz`, `decmpfs-apfs.sha256` | APFS written by macOS, files in every decmpfs method | what macOS read back through the mounted volume |
| `apfs-converted.sparseimage.gz`, `apfs-converted.sha256` | APFS encrypted in place by macOS (password `qnxprobe-apfs-convert`) | hashes recorded when made |

The keys and passwords were made for these fixtures and open nothing else.
| `btrfs-none/zlib/lzo/zstd.img.gz`, `btrfs.src.sha256` | single-device Btrfs, uncompressed and compressed, from qnxprobe's own fixtures (github.com/abrignoni/qnxprobe, commit e9122fa8, MIT) | SHA-256 of each of the 572 source files |
| `luks-fixture.sha256` | SHA-256 of each file of the ext4 inside `luks1-cbc` and `luks2-argon2id`, as sha256sum read it from the mounted volume (the other tool's tests/fixtures, 2026.5.7, MIT) | used by `tests/test_luks_unlock.py` |

# Unraid USB Mirror

Real-time mirroring of your Unraid USB boot drive to a backup USB device using inotify.

## How It Works

1. **Initial sync**: On startup, syncs all files from primary to backup
2. **Real-time monitoring**: Uses inotify to detect changes instantly
3. **Automatic sync**: New/modified files are copied immediately
4. **Cleanup**: Deleted files are removed from backup

## Table of Contents

- [Requirements](#requirements)
- [Notifications](#notifications)
- [Configuration](#configuration)
- [Example](#example)

## Requirements

- Unraid server
- Backup USB drive mounted somewhere (e.g., `/mnt/disks/UNRAID-backup`)
- Python 3 plugin with pip with inotify and colorlog installed

## Notifications

The script sends Unraid notifications for:
- Service started/stopped
- Sync errors (throttled to every 5 minutes)
- Critical errors (USB not found, service crash)

Check your Unraid notification center for alerts.

## Configuration

Edit these variables at the top of the script:

```python
BOOT_USB = "/boot"                    # Your Unraid USB
BACKUP_DEST = "/mnt/disks/UNRAID-backup"          # Your backup USB mount
EXCLUDED_PATHS = ['System Volume Information']  # Paths to ignore
```
### Example

```
[2025-11-16 09:46:10] [INFO] Initial sync completed: 575 changes made
[2025-11-16 09:46:10] [INFO] Starting inotify monitoring...
[2025-11-16 09:46:10] [INFO] Watching: /boot
[2025-11-16 09:47:16] [DEBUG] Detected change: passwd
[2025-11-16 09:47:16] [INFO] Synced: config/passwd
[2025-11-16 09:47:16] [DEBUG] Detected change: shadow
[2025-11-16 09:47:16] [INFO] Synced: config/shadow
[2025-11-16 09:47:16] [DEBUG] Detected change: smbpasswd
[2025-11-16 09:47:16] [INFO] Synced: config/smbpasswd
```

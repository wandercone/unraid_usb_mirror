#!/usr/bin/env python3
import os
import sys
import shutil
import logging
import argparse
import subprocess
from colorlog import ColoredFormatter
from pathlib import Path
import inotify.adapters
import threading
import time

# Configuration
BOOT_USB = "/boot"  # Default Unraid USB mount point
BACKUP_DEST = "/mnt/remotes/local_backups/usb_backup" # Destination location 
EXCLUDED_PATHS = ['System Volume Information']

# Global states
sync_lock = threading.Lock()
error_count = 0
last_error_notification = 0
notification_cooldown = 300  # 5 minutes between error notifications
dry_run_mode = False

# Setting up logging
handler = logging.StreamHandler()
handler.setFormatter(ColoredFormatter(
    fmt='%(log_color)s[%(asctime)s] [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    log_colors={
        'DEBUG':    'cyan',
        'INFO':     'green',
        'WARNING':  'yellow',
        'ERROR':    'red',
        'CRITICAL': 'bold_red',
    }
))

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
logger.addHandler(handler)
logger.propagate = False

def notify_host(subject, message, icon="normal"):
    global last_error_notification
    current_time = time.time()

    if current_time - last_error_notification < notification_cooldown:
        logger.debug("Notification cooldown active, skipping notification.")
        return

    if dry_run_mode:
        logger.info(f"- DRY RUN - Would send notification: [{subject}] {message}")
        return
    
    try:
        subprocess.run([
            "/usr/local/emhttp/webGui/scripts/notify",
            "-e", "USB Mirror Service",
            "-s", subject,
            "-d", message,
            "-i", icon
        ], check=True)
        last_error_notification = current_time
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to send notification: {e}")
    except FileNotFoundError:
        logger.warning("Unraid notify script not found")


def is_mounted(path):
    path = os.path.normpath(path)
    # Use df -T to return the filesystem type
    try:
        result = subprocess.run(['df', '-T'], stdout=subprocess.PIPE, text=True)
        df_output = result.stdout.splitlines()

        for line in df_output[1:]:
            parts = line.split()
            fstype = parts[1]
            if fstype not in ['rootfs', 'tmpfs', 'devtmpfs', 'efivars', 'overlay']:
                return True

    except Exception as e:
        logger.critical(f"Error while checking mount status: {e}")
        return False
    
    return False

def check_paths():
    if not is_mounted(BOOT_USB):
        msg = f"Boot drive is not mounted at {BOOT_USB}"
        logger.error(msg)
        notify_host("USB Mirror Error - Boot drive not Mounted", msg, "alert")
        return False
    
    if not os.path.exists(BACKUP_DEST):
        msg = f"Destination location ({BACKUP_DEST}) does not exist"
        logger.error(msg)
        notify_host("USB Mirror Error - Backup destination Not Found", msg, "alert")
        return False
    
    elif not is_mounted(BACKUP_DEST):
        msg = f"Destination location ({BACKUP_DEST}) is not properly mounted"
        logger.error(msg)
        notify_host("USB Mirror Error - Backup destination is not a mount", msg, "alert")
        return False
    
    return True

def should_exclude(path):
    for excluded in EXCLUDED_PATHS:
        if excluded in path:
            return True
    return False

def get_backup_path(primary_path):
    rel_path = os.path.relpath(primary_path, BOOT_USB)
    return os.path.join(BACKUP_DEST, rel_path)

def sync_file(src):
    if should_exclude(src):
        return
        
    dst = get_backup_path(src)
    
    try:
        with sync_lock:
            if dry_run_mode:
                logger.info(f"- DRY RUN - Would sync: {os.path.relpath(src, BOOT_USB)}")
            else:
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(src, dst)
                logger.info(f"Synced: {os.path.relpath(src, BOOT_USB)}")
    except Exception as e:
        logger.error(f"Error syncing {src}: {e}")
        notify_host("USB Mirror Error - Sync failure", f"Failed to sync {os.path.relpath(src, BOOT_USB)}: {str(e)}", "alert")

def remove_file(primary_path):
    backup_path = get_backup_path(primary_path)
    
    if should_exclude(backup_path):
        return
        
    try:
        with sync_lock:
            if os.path.exists(backup_path):
                if dry_run_mode:
                    logger.info(f"- DRY RUN - Would remove: {os.path.relpath(backup_path, BACKUP_DEST)}")
                else:
                    os.remove(backup_path)
                    logger.info(f"Removed: {os.path.relpath(backup_path, BACKUP_DEST)}")
                
                # Remove empty parent directories
                if not dry_run_mode:
                    parent = os.path.dirname(backup_path)
                    while parent != BACKUP_DEST:
                        try:
                            if not os.listdir(parent):
                                os.rmdir(parent)
                                logger.debug(f"Removed empty directory: {parent}")
                            else:
                                break
                        except:
                            break
                        parent = os.path.dirname(parent)
    except Exception as e:
        logger.error(f"Error removing {backup_path}: {e}")
        notify_host("USB Mirror Error - Deletion failure", f"Failed to remove {os.path.relpath(backup_path, BACKUP_DEST)}: {str(e)}", "alert")

def remove_directory(primary_path):
    backup_path = get_backup_path(primary_path)
    
    if should_exclude(backup_path):
        return
        
    try:
        with sync_lock:
            if os.path.exists(backup_path):
                if dry_run_mode:
                    logger.info(f"- DRY RUN - Would remove directory: {os.path.relpath(backup_path, BACKUP_DEST)}")
                else:
                    shutil.rmtree(backup_path)
                    logger.info(f"Removed directory: {os.path.relpath(backup_path, BACKUP_DEST)}")
    except Exception as e:
        logger.error(f"Error removing directory {backup_path}: {e}")
        notify_host("USB Mirror Error - Deletion failure", f"Failed to remove {os.path.relpath(backup_path, BACKUP_DEST)}: {str(e)}", "alert")

def initial_sync():
    logger.info("Performing initial sync...")   
    changes = 0
    
    # Sync all files from primary to backup
    for root, dirs, files in os.walk(BOOT_USB):
        # Filter out excluded directories
        dirs[:] = [d for d in dirs if not should_exclude(os.path.join(root, d))]
        
        for file in files:
            primary_path = os.path.join(root, file)
            backup_path = get_backup_path(primary_path)
            
            if should_exclude(primary_path):
                continue
            
            # Check if sync is needed
            needs_sync = False
            
            if not os.path.exists(backup_path):
                needs_sync = True
            else:
                try:
                    primary_stat = os.stat(primary_path)
                    backup_stat = os.stat(backup_path)
                    
                    if (primary_stat.st_mtime != backup_stat.st_mtime or 
                        primary_stat.st_size != backup_stat.st_size):
                        needs_sync = True
                except:
                    needs_sync = True
            
            if needs_sync:
                sync_file(primary_path)
                changes += 1
    
    # Remove files from backup that don't exist in primary
    if not dry_run_mode:
        for root, dirs, files in os.walk(BACKUP_DEST):
            for file in files:
                backup_path = os.path.join(root, file)
                rel_path = os.path.relpath(backup_path, BACKUP_DEST)
                primary_path = os.path.join(BOOT_USB, rel_path)
                
                if not os.path.exists(primary_path):
                    try:
                        os.remove(backup_path)
                        logger.info(f"Removed orphaned file: {rel_path}")
                        changes += 1
                    except Exception as e:
                        logger.error(f"Error removing {backup_path}: {e}")
    
    logger.info(f"Initial sync completed: {changes} changes made")

def start_monitoring():
    if not check_paths():
        sys.exit(1)
    
    # Perform initial sync
    initial_sync()
    
    logger.info(f"Starting inotify monitoring of {BOOT_USB}")
    
    # Create inotify instance
    i = inotify.adapters.InotifyTree(BOOT_USB)
    
    # Throttle path checks
    last_path_check = time.time()
    path_check_interval = 60  # Check every 60 seconds
    
    try:
        for event in i.event_gen(yield_nones=False):
            # Periodic path validation
            current_time = time.time()
            if current_time - last_path_check >= path_check_interval:
                if not check_paths():
                    sys.exit(1)
                last_path_check = current_time
            
            (_, type_names, path, filename) = event
            
            full_path = os.path.join(path, filename)
            
            if should_exclude(full_path):
                continue
                
            # File created or modified
            if 'IN_CLOSE_WRITE' in type_names or 'IN_MOVED_TO' in type_names:
                if os.path.isfile(full_path):
                    logger.debug(f"Detected change: {filename}")
                    sync_file(full_path)

            # File deleted
            elif 'IN_DELETE' in type_names or 'IN_MOVED_FROM' in type_names:
                logger.debug(f"Detected deletion: {filename}")
                remove_file(full_path)

            # Directory deleted
            elif 'IN_DELETE_SELF' in type_names or 'IN_MOVED_FROM' in type_names:
                if not os.path.exists(full_path):
                    logger.debug(f"Detected directory deletion: {filename}")
                    remove_directory(full_path)

            # New directory created
            elif 'IN_CREATE' in type_names and 'IN_ISDIR' in type_names:
                backup_path = get_backup_path(full_path)
                try:
                    if dry_run_mode:
                        logger.debug(f"- DRY RUN - Would create directory: {filename}")
                    else:
                        os.makedirs(backup_path, exist_ok=True)
                        logger.debug(f"Created directory: {filename}")
                except Exception as e:
                    logger.error(f"Error creating directory {backup_path}: {e}")

    except KeyboardInterrupt:
        logger.info("Monitoring stopped by user")

    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        notify_host(
            "USB Mirror Critical Error",
            f"Service crashed with error: {str(e)}",
            "alert"
        )
        raise

def main():
    global dry_run_mode
    
    parser = argparse.ArgumentParser(description="Unraid USB Mirroring Tool")
    parser.add_argument("--dry-run", action="store_true", help="Show what would happen without making changes")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    args = parser.parse_args()

    if args.debug:
        logger.setLevel(logging.DEBUG)
        logger.debug("Debug logging enabled.")
    
    dry_run_mode = args.dry_run

    logger.debug(f"Boot Device:          {BOOT_USB}")
    logger.debug(f"Backup Destination:   {BACKUP_DEST}")
    
    start_monitoring()

if __name__ == "__main__":
    main()

#!/usr/bin/env bash

set -e

# If the environment variable SMB_SHARE_URL is set, mount the SMB share.
# Environment variables:
# - SMB_SHARE_URL: The URL of the SMB share to mount.
# - SMB_USER: The username to use for the SMB share.
# - SMB_PASSWORD: The password to use for the SMB share.
# - SMB_MOUNT_PATH: The path to mount the SMB share to. Defaults to "/data".
echo "Checking for SMB mount..."
if [ -n "$SMB_SHARE_URL" ]; then
  echo "Mounting SMB share $SMB_SHARE_URL ..."

  mkdir /mnt/datafs

  # Create the credentials file
  if [ ! -d "/etc/smbcredentials" ]; then
    mkdir /etc/smbcredentials
  fi
  if [ ! -f "/etc/smbcredentials/datafs.cred" ]; then
      echo "username=$SMB_USER" >> /etc/smbcredentials/datafs.cred
      echo "password=$SMB_PASSWORD" >> /etc/smbcredentials/datafs.cred
  fi
  chmod 600 /etc/smbcredentials/datafs.cred

  # Mount the share
  echo "$SMB_SHARE_URL /mnt/datafs cifs nofail,credentials=/etc/smbcredentials/datafs.cred,dir_mode=0777,file_mode=0777,serverino,nosharesock,actimeo=30,noperm" >> /etc/fstab
  mount -t cifs "$SMB_SHARE_URL" /mnt/datafs -o credentials=/etc/smbcredentials/datafs.cred,dir_mode=0777,file_mode=0777,serverino,nosharesock,actimeo=30,noperm

  # Link the mount point to the configured path, or just "/data"
  mount_point=${SMB_MOUNT_PATH:-/data}
  ln -s /mnt/datafs $mount_point

  # Ensure the mount point is writable for everyone
  chmod 777 /mnt/datafs
  chmod 777 $mount_point
else
  echo "SMB_SHARE_URL not set, skipping SMB mount."
fi

# Delegate to the real init script
exec /init

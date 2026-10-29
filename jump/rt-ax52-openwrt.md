# Flashing OpenWrt on an ASUS RT-AX52

The RT-AX52 (MediaTek MT7981, 256 MB RAM, 128 MB flash) is officially supported by OpenWrt. It works as router A or router B. See the [OpenWrt device page](https://openwrt.org/toh/asus/rt-ax52).

## Normal path

1. Download both images for **ASUS RT-AX52** from the [OpenWrt Firmware Selector](https://firmware-selector.openwrt.org/?target=mediatek/filogic&id=asus_rt-ax52):
   - `...-asus_rt-ax52-initramfs.trx`
   - `...-asus_rt-ax52-squashfs-sysupgrade.bin`
2. Connect a PC by cable to a **LAN** port. Leave WAN unplugged.
3. Open `http://192.168.50.1`, finish the setup wizard manually (do not let it update online), then go to Administration → Firmware Upgrade → Upload and select `initramfs.trx`.
4. After the reboot, open `http://192.168.1.1` (OpenWrt, running from RAM), go to System → Backup / Flash Firmware and flash `squashfs-sysupgrade.bin` without keeping settings.
5. Set a root password and plug in WAN.

## "Firmware upgrade unsuccessful. This may result from incorrect image or error transmission."

Recent ASUS firmware rejects the OpenWrt image. Work through these steps in order:

1. **Check the file.** It must be `initramfs.trx`, not `initramfs-kernel.bin` or `sysupgrade.bin`. The label under the router must say RT-AX52.
2. **Use a snapshot image.** The 24.10.2 stable `initramfs.trx` was reported not to install over stock firmware ([mjul/openwrt-asus-rt-ax52](https://github.com/mjul/openwrt-asus-rt-ax52)). Flash a snapshot `initramfs.trx` first, then sysupgrade to stable.
3. **Downgrade the stock firmware** ([OpenWrt forum](https://forum.openwrt.org/t/asus-rt-ax52-support/199849?page=2)):
   1. Download the oldest firmware from the [ASUS support page](https://www.asus.com/networking-iot-servers/wifi-routers/asus-wifi-routers/rt-ax52/helpdesk_download?model2Name=RT-AX52) (for example `3.0.0.4.388_32682`) and the ASUS Firmware Restoration utility (Windows only).
   2. Give the PC a static IP `192.168.1.10/24`, plug the cable into a LAN port, and disable Wi-Fi and other adapters.
   3. Enter rescue mode: power off, hold Reset, power on, and keep holding until the power LED blinks slowly.
   4. In Firmware Restoration, select the old firmware and click Upload. Wait for "Successfully recovered the system".
   5. Switch the PC back to DHCP, open `http://192.168.50.1`, finish the wizard offline, and upload `initramfs.trx` again.
4. **Last resorts.** Upload `initramfs.trx` directly in Firmware Restoration, or use a serial console (USB-UART) and TFTP boot from U-Boot as described on the [OpenWrt device page](https://openwrt.org/toh/asus/rt-ax52).

## Packages

OpenWrt 25.12 uses `apk`, and 24.10 uses `opkg`. The jump `setup.sh` scripts detect which one is present and install `wireguard-tools` and `luci-proto-wireguard` themselves.

# DeskPrinter printer open components

This export contains only the GPL printer bridges, the MIT filament publisher,
the source of the vendored BSD/MIT qrcode dependency, and a generic MIT build helper.
It contains no capture coordinator, private Studio backend, credentials, unit IDs,
photos, backups, original firmware, Desktop/mobile application or customer settings.

The matching distribution compiles these open modules as ARMv7 ELF/CPython3.9
extensions. Their preferred sources and license notices stay available here.
Use Cython3.1.6, Zig0.15.2 and compatible CPython3.9 ARM hard-float development
headers. Full build flags are in build-information.json. The factory interpreter,
Pillow, fonts and native firmware remain installed dependencies, not redistributed
vendor packages. Review the exact package's compliance before commercial delivery.

On a root-accessible printer, replace your chosen open extension in its installed
location (stop/restart its host safely). No vendor package signing key is required
to run your modified GPL bridge. Removing its .so selects the supplied .py source
under the host interpreter's regular loader. Desktop updates detect your changes
and stop instead of overwriting them. Keep matching source/build/installation
information and license notices when redistributing your modified version.

This directory is prepared for a separate public Git repository. It has not been
published automatically. It is not the private product repository or a firmware
image. Hardware and integration acceptance are a separate step.

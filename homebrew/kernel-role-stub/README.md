# PSP Dock kernel role stub — SOLO COMPILACION, NO INSTALAR

Maqueta del contrato de futuro componente kernel para ARK-5.
GetStatus devuelve versión 0, capacidades 0 y rol inactivo;
RequestRole devuelve siempre -1, nunca cambia USB, HID o FAT.

No importa USB ni toca flash, SAVEDATA o plugins de arranque.
Se compila mediante homebrew/kernel-role-stub/build-wsl.sh.
El binario NO se incluye en los instaladores para PSP.

SHA-256:
759ff121788b4fbbffd98a2d903f20ecb2163bdcfb3269f4d1bf1b654cd62172

No interpretarlo como un gestor de roles funcional. Es exclusivamente
una prueba de empaquetado kernel para una fase futura.

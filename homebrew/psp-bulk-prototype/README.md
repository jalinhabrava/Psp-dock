# PSP Dock — descriptor y protocolo USB BULK experimentales

Este directorio contiene **código C puro no instalable**:
pdb2_wire.c/h y composite_layout.c. No hay registro de driver,
arranque PSP, modificación de flash ni montaje de FAT.

Pruebas en WSL Ubuntu-22.04:

    gcc -std=c11 -O2 -Wall -Wextra -Werror pdb2_wire.c test_pdb2.c -o /tmp/pspdock-pdb2-test
    /tmp/pspdock-pdb2-test
    rm -f /tmp/pspdock-pdb2-test
    bash build-wsl.sh

build-wsl.sh usa psp-gcc para compilar un objeto MIPS temporal
no ejecutable, inspecciona la arquitectura y lo borra.

PDB2 usa tramas con CRC32 y sesiones; no es cifrado, MAC ni
identificación del firmware. Actualmente no existe transporte
USB hardware para estas tramas. Ver el documento raíz
ONE-CABLE-USB-BULK.md y sus requisitos de seguridad.

# PSP Dock — ensayo integral 100 % simulado (solo PC)

`experience_simulator.py` recorre la misma máquina de estados pura de
`dock_experience.Experience` con un inventario ficticio. **No detecta, monta,
expulsa ni escribe la PSP, la Memory Stick o los guardados reales.** Tampoco
ejecuta PPSSPP, activa un gamepad HID ni habla con el firmware. Solo genera
eventos sintéticos, recibe las *intenciones* de la FSM y registra qué transición
habría solicitado el producto si un adaptador físico pudiera demostrarla.

## Ejecución

Desde la raíz del proyecto, con Python:

```powershell
python experience_simulator.py --choice copy
python experience_simulator.py --choice skip
python -m unittest discover -s tests -p test_experience_simulator.py -v
```

Ambos recorridos imprimen JSON en stdout, sin crear archivos. Las salidas llevan
el marcador obligatorio `SYNTHETIC_ONLY_NO_HARDWARE_EVIDENCE` tanto en el
checkpoint como en cada transición. La opción `copy` finge dos juegos ausentes
copiados y verificados; `skip` conserva los juegos ausentes sin copiarlos.

En ambos casos se elige expresamente una resolución de conflicto, se autoriza
la copia de partidas, se solicita el paso de USBSTOR a HID, se ensayan el lock
exclusivo, flush, ACK de firmware, paro de almacenamiento y aparición HID,
se lanzan dos juegos separados con retorno al XMB, se pide volver al
almacenamiento original, se aprueba y verifica la devolución de saves y se
autoriza la desconexión cuando el volumen está liberado en la **simulación**.

El simulador solo conserva un inventario de identificadores ficticios y banderas
de verificación. **No realiza copias reales ni calcula hashes de ISO/saves**:
el `inventory_tag` se deriva de los nombres del inventario de prueba. El
`RoleBridgeProof` que permite avanzar en `start_dock` se crea únicamente como
variable temporal local, con datos **inventados para ejercitar la FSM**. No se
exporta ni se guarda en el JSON. Nunca constituye una prueba de que el kernel,
el FAT o un ACK USB/HID funcionen en la PSP física.

## Fallos ensayados

Las pruebas comprueban que no se modifica el estado ni se emite una intención
ante identidad Sony incorrecta, varios volúmenes, inventario cambiado,
copias sin verificar, journals pendientes, conflictos de partidas sin decisión,
sin consentimiento para sincronizar, ausencia de bridge/ACK, fallo de lock,
flush o exclusión FAT, HID de otra sesión, almacenamiento todavía montado,
juego ausente, PPSSPP abierto, orden de retorno duplicada, Memory Stick
distinta, backup sin verificar y desconexión prematura.

Si se simula una desconexión durante una fase insegura, el estado pasa a
`halted` y no se inicia ninguna copia o retorno adicional. Un checkpoint JSON
puede recuperarse, pero sigue bloqueado: `restart_after_interruption()` requiere
la identidad original y vuelve a inventariar y solicitar autorizaciones desde
cero. El indicador de partidas pendientes en PC se conserva; **el simulador no
afirma resolver el retorno físico de esos guardados tras la interrupción**.
Esa recuperación sigue requiriendo una implementación y pruebas específicas
del adaptador real.

## Límite de la evidencia

Este ensayo comprueba la coherencia de la secuencia y sus barreras lógicas.
No valida la implementación del bridge de kernel, la exclusividad efectiva de
FAT en Windows/PSP, ACK vivos, el controlador HID, los guardados del juego ni
la fiabilidad de una transición física USBSTOR ↔ HID. El flujo de hardware
real sigue bloqueado hasta disponer de esa evidencia.

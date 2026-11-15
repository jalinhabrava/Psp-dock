# SOCOM II Legacy: fondo e iconos XMB

Inspección del Chrome real del usuario, 15-11-2026. Launcher observado: v0.4.22.

El fondo visible se dibuja con **Canvas 2D**, dentro de `#backdrop > .cx-sheets`.
El CSS coloca ese lienzo a pantalla completa; JavaScript construye el efecto.
Combina un degradado vertical, un resplandor radial bajo, bandas de curvas
sinusoidales translúcidas con mezcla aditiva, líneas finas, puntos discretos
y una viñeta oscura. La fase cambia suavemente al moverse entre categorías.
Usa `requestAnimationFrame`, limita la resolución a DPR 2 y suspende el dibujo
cuando la página está oculta o el juego está ejecutándose; respeta movimiento
reducido. La base cromática observada es `#000e11`.

Los símbolos inferiores son **SVG inline**, con color heredado, sin relleno,
trazo de 1.5 y trazos interiores de 2 con extremos redondeados. Los glifos
de acción usan un lienzo SVG 26×26 dentro de `.cx-btn`; los botones L1/R1
son formas CSS `.cx-shoulder` con borde de 2 px y radios superiores mayores.
Los colores observados son cruz `#5fb3bf`, triángulo `#3fae7f` y cuadrado
`#d06fb0`. La tipografía de etiquetas es Oswald con alternativas condensadas.

La barra `.cx-legend` separa acciones de indicadores de disco, mando y micrófono.
Cada acción une símbolo y etiqueta; los indicadores pequeños añaden un punto
de estado. El espaciado constante y el trazo compartido les dan coherencia.
Sus versiones adaptables recortan etiquetas, conservan símbolos y gestionan
el ancho disponible. No son emojis ni una colección de PNG.

Para PSP Dock conviene adoptar estos principios: ondas suaves de poco contraste,
animación pausada durante el juego, símbolos vectoriales consistentes y ayudas
que cambien entre PSP y Xbox. Esto es una propuesta derivada del análisis;
esta PR integra el inventario, sin aplicar un rediseño visual del fondo.

Fuentes observadas: [SOCOM Legacy](https://socomlegacy.com/),
[estilos compartidos](https://socomlegacy.com/assets/pageVersion-C-GnomJ2.css),
[estilos de consola](https://socomlegacy.com/assets/console-BPjmEr2l.css),
[módulo de la interfaz y fondo](https://socomlegacy.com/assets/app-PzKcZecm.js).
Los nombres con hash corresponden a la versión inspeccionada y pueden cambiar.

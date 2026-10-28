# Mascota de escritorio de Messi — Guía en español

[中文](zh-CN.md) · [English](en.md) · [Español](es.md) · [日本語](ja.md) · [한국어](ko.md)

Un compañero de peluche con la camiseta argentina número 10. Ambas versiones incluyen los últimos gestos: manos grandes junto a las orejas, expresión seria sin parpadear y un sobre más grande sostenido con los ojos abiertos. Las imágenes adaptan una referencia de peluche aportada por el usuario; no implican una colaboración oficial.

## Elige una versión

| Versión | Descarga | Requisitos |
| --- | --- | --- |
| Conectada a Codex | [Descargar ZIP del Skill](https://github.com/roman0102/chongzhi-chibaole/raw/refs/heads/main/downloads/messi-codex-skill-windows-v1.0.0.zip) | Windows; Codex abierto y con sesión iniciada; Python 3.10+, Tk y Pillow |
| Independiente en bucle | [Descargar ZIP del EXE sin conexión](https://github.com/roman0102/chongzhi-chibaole/raw/refs/heads/main/downloads/messi-offline-windows-v1.0.0.zip) | Windows de 64 bits; sin Codex, instalación de Python, cuenta ni internet |

Probado en el equipo actual con Windows 11. No se ofrecen paquetes para macOS, Linux ni móviles. Las guías están traducidas; los menús del programa siguen en chino.

## Versión independiente: descomprime y juega

1. Descarga y descomprime por completo el ZIP independiente. Haz doble clic en `Messi-Offline.exe`.
2. Cada seis segundos cambia de gesto: reposo → regate a la derecha → celebración con las manos en las orejas → beso a la copa → mostrar el sobre → gesto de hablar → regate a la izquierda → expresión de desconcierto. Después se repite.
3. Arrastra con el botón izquierdo para cambiarlo de lugar. Haz clic o clic derecho para abrir el menú. Se desplaza al regatear y se detiene al poner el cursor encima.
4. “选择并保持动作” significa **elegir y mantener un gesto**. Activa “自动切换动作” para reanudar el bucle. “下一个动作” significa **siguiente gesto**.
5. Cierra con Esc, la × que aparece al pasar el cursor o “退出玩偶” (**salir**). Abrir otra vez el EXE no crea una segunda instancia independiente.

**El sobre, la copa y los bocadillos de la versión independiente son animaciones programadas: no representan correos, cuota ni tareas reales.** No se conecta a Codex ni a Gmail, ni crea tareas programadas o entradas de inicio automático. El EXE incluye el entorno de ejecución y las imágenes; no necesita los archivos fuente.

## Versión conectada a Codex

1. Descomprime el ZIP conectado. Entrega a Codex la carpeta `quota-pet` y pídele:

   > Sigue el SKILL.md de esta carpeta para ejecutar assets/messi/skin.json, conectar mi cuota real y el estado de trabajo local, y crear un acceso directo con el icono del peluche. Deja Gmail desactivado.

2. También puedes ejecutar, dentro de `quota-pet`, con tu Python instalado:

```powershell
python -m pip install Pillow
python scripts/quota_pet.py --skin assets/messi/skin.json --background
python scripts/create_shortcut.py --skin assets/messi/skin.json
```

3. Pasa el cursor para ver la tarjeta de estado. El menú permite probar las poses. “刷新真实额度” (**actualizar la cuota real**) sale de la simulación. El acceso directo de Windows usa el icono del peluche.

| Condición real | Gesto |
| --- | --- |
| Quieto, queda el 100 % | Besa la copa dorada |
| Quieto, queda entre el 80 % y menos del 100 % | Se endereza de golpe, manos grandes en las orejas, serio y sin parpadear |
| Queda el 0 % | Mira desconcertado, hace un puchero y gira la cabeza; deja de pasear y muestra ¿Qué mirás, bobo? |
| Trabajando o paseando en reposo | Regatea a izquierda o derecha |
| Esperando tu respuesta o aprobación | Gesto de hablar con ¿Qué mirás, bobo? |
| Correo nuevo detectado mediante una consulta de Gmail autorizada por separado | Muestra el sobre grande con expresión seria y ojos abiertos |

En modo real, la espera y los errores tienen prioridad sobre el correo; el correo tiene prioridad sobre las poses de cuota. El trabajo y los paseos usan el regate. La vista previa de cuota muestra la pose seleccionada sin alterar el estado real de trabajo. Una cuota desconocida no se interpreta como cero. Se consulta cada cinco minutos por defecto; se muestra la ventana con menor porcentaje restante, separando los créditos adicionales.

Gmail está **desactivado por defecto**. Conectar el complemento no autoriza un seguimiento continuo. Las consultas requieren autorización expresa, se ejecutan periódicamente mediante Codex, consumen cuota de Codex y no son notificaciones instantáneas. Solo se usan los metadatos necesarios; confirmar un aviso local no marca el correo como leído. Cerrar la mascota no detiene la tarea de consulta: también debes pausarla. Consulta `references/gmail.md` dentro del paquete.

La animación, la lectura de cuota y el seguimiento del trabajo no llaman a un modelo ni necesitan otra API key. El estado de trabajo usa una interfaz local de compatibilidad que puede requerir ajustes tras actualizar Codex. La instantánea inicial puede incluir contenido de conversaciones; el programa conserva solo campos de estado en memoria y descarta los textos sin guardar la instantánea original.

## Vista previa y ayuda

<img src="previews/trophy.gif" width="126" alt="Beso a la copa dorada"><img src="previews/ears.gif" width="126" alt="Celebración seria con manos grandes en las orejas"><img src="previews/mail.gif" width="126" alt="Mostrar un sobre grande sin parpadear">

Las vistas previas se exportan de los fotogramas reales, no de datos de una cuenta en directo. “¿Qué mirás, bobo?” aparece como texto; no hay voz.

- Para ver una pose, usa “动作试玩” (**probar gestos**) en la versión conectada o “选择并保持动作” en la independiente.
- Si no se puede leer la cuota, revisa la sesión de Codex; el estado debe mostrarse como desconocido.
- Para desinstalar, cierra la mascota y elimina los archivos y el acceso directo. Si activaste Gmail, pausa antes su tarea.
- Son ventanas de escritorio independientes del menú de mascotas integrado en Codex. Ambas versiones pueden coexistir.

No se ha declarado una licencia que permita el uso comercial sin restricciones del código o de las imágenes del personaje.

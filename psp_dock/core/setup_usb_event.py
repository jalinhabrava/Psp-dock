"""Retired PnP pilot: only preview/removal remains available from the CLI.

The 410 subscription did not recur on the physically verified 0207
connections. David chose opening Windows PSP Dock manually and waiting
for the console in that window; do not replace this pilot with a service
or a login watcher. --install is intentionally rejected before mutation.

Historical implementation retained below for tests/legacy cleanup:
Windows Kernel-PnP
Configuration Event 410 can indicate configuration of a Sony HID,
but may be emitted only on initial driver setup: do not promise it
fires on every connection without a physical validation.

The subscribed event may be caused by unrelated device configuration;
the one-shot action revalidates VID_054C/PID_01CB before opening XMB.
No Windows-wide audit policy, driver, service or AutoRun is modified.
"""
from __future__ import annotations

import argparse
import getpass
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

TASK_NAME="PSP Dock - USB HID"
EVENT_CHANNEL="Microsoft-Windows-Kernel-PnP/Configuration"
EVENT_ID=410
NS="http://schemas.microsoft.com/windows/2004/02/mit/task"
ET.register_namespace("",NS)


def task_xml(*, python_exe:Path, event_script:Path, user_sid:str)->str:
    python_exe=Path(python_exe).resolve()
    event_script=Path(event_script).resolve()
    if (not python_exe.name.lower() in ("python.exe","pythonw.exe")
            or not event_script.name=="on_psp_hid_arrival.py"
            or not user_sid.startswith("S-1-5-21-")
            or any(x in str(python_exe)+str(event_script) for x in ('"','\r','\n'))):
        raise ValueError("Task XML fields do not match the PSP Dock installer.")
    tag=lambda name:f"{{{NS}}}{name}"
    root=ET.Element(tag("Task"),{"version":"1.4"})
    registration=ET.SubElement(root,tag("RegistrationInfo"))
    ET.SubElement(registration,tag("Description")).text=(
        "On-demand PSP Dock XMB after HID PnP event; validates VID/PID first.")
    triggers=ET.SubElement(root,tag("Triggers"))
    trigger=ET.SubElement(triggers,tag("EventTrigger"))
    ET.SubElement(trigger,tag("Enabled")).text="true"
    selector=(
        f'<QueryList><Query Id="0" Path="{EVENT_CHANNEL}">'
        f'<Select Path="{EVENT_CHANNEL}">*[System[(EventID={EVENT_ID})]]'
        '</Select></Query></QueryList>'
    )
    ET.SubElement(trigger,tag("Subscription")).text=selector
    ET.SubElement(trigger,tag("Delay")).text="PT2S"
    principals=ET.SubElement(root,tag("Principals"))
    principal=ET.SubElement(principals,tag("Principal"),{"id":"Author"})
    ET.SubElement(principal,tag("UserId")).text=user_sid
    ET.SubElement(principal,tag("LogonType")).text="InteractiveToken"
    ET.SubElement(principal,tag("RunLevel")).text="LeastPrivilege"
    settings=ET.SubElement(root,tag("Settings"))
    for name,val in (
        ("MultipleInstancesPolicy","IgnoreNew"),
        ("DisallowStartIfOnBatteries","false"),
        ("StopIfGoingOnBatteries","false"),
        ("AllowHardTerminate","true"),
        ("StartWhenAvailable","false"),
        ("RunOnlyIfNetworkAvailable","false"),
        ("Enabled","true"),("Hidden","false"),
        # After the event arrives, start_dock.launch() legitimately keeps
        # the Windows XMB service alive for the operator's whole session.
        # This task has NO login/boot trigger and starts only on an event.
        ("ExecutionTimeLimit","PT0S"),
        ("Priority","7")
    ):
        ET.SubElement(settings,tag(name)).text=val
    actions=ET.SubElement(root,tag("Actions"),{"Context":"Author"})
    action=ET.SubElement(actions,tag("Exec"))
    ET.SubElement(action,tag("Command")).text=str(python_exe)
    ET.SubElement(action,tag("Arguments")).text=(
        '"'+str(event_script)+'" --wait-seconds 10')
    ET.SubElement(action,tag("WorkingDirectory")).text=str(event_script.parent)
    return ET.tostring(root,encoding="unicode")


def current_user_sid()->str:
    import csv,io
    result=subprocess.run(["whoami","/user","/fo","csv","/nh"],
                          text=True,capture_output=True,timeout=8)
    if result.returncode!=0:
        raise ValueError("No se pudo identificar el usuario local Windows.")
    rows=list(csv.reader(io.StringIO(result.stdout)))
    if len(rows)!=1 or len(rows[0])<2 or not rows[0][1].startswith("S-1-5-21-"):
        raise ValueError("SID de usuario Windows no reconocido.")
    return rows[0][1]


def install_task(xml:str, *, run=subprocess.run):
    with tempfile.TemporaryDirectory(prefix="pspdock-event-") as path:
        src=Path(path)/"PSPDockTask.xml"
        src.write_text(xml,encoding="utf-16")
        result=run(["schtasks.exe","/Create","/TN",TASK_NAME,
                    "/XML",str(src),"/F"],capture_output=True,text=True,
                   timeout=25)
        if result.returncode!=0:
            raise ValueError(
                "Windows no autorizó la tarea HID: "+result.stderr.strip()[:250])


def remove_task(*,run=subprocess.run):
    result=run(["schtasks.exe","/Delete","/TN",TASK_NAME,"/F"],
               capture_output=True,text=True,timeout=20)
    return result.returncode==0


def main()->int:
    parser=argparse.ArgumentParser(description="Inicio puntual PSP Dock por evento USB")
    action=parser.add_mutually_exclusive_group()
    action.add_argument("--install",action="store_true")
    action.add_argument("--remove",action="store_true")
    args=parser.parse_args()
    if args.install:
        print("Piloto de autoarranque retirado. Abre Iniciar-PSP-Dock.cmd "
              "y conecta la PSP cuando aparezca la pantalla de espera.")
        return 1
    try:
        if os.name!="nt":
            raise ValueError("Solo compatible con Windows.")
        if args.remove:
            print("Tarea eliminada" if remove_task() else "No se encontró tarea.")
            return 0
        exe=Path(sys.executable).with_name("pythonw.exe")
        script=Path(__file__).resolve().parents[2] / "tools" / "system" / "on_psp_hid_arrival.py"
        if not exe.is_file() or not script.is_file():
            raise ValueError("Falta Pythonw o el script de detección HID.")
        xml=task_xml(python_exe=exe,event_script=script,
                     user_sid=current_user_sid())
        if not args.install:
            print("RETIRADO: el flujo actual comienza abriendo PSP Dock "
                  "en Windows. No se ha registrado ninguna tarea.")
            return 0
        install_task(xml)
        print("Tarea del usuario registrada. No hay monitor permanente.")
        print("Validar que el evento se repita al desconectar/reconectar PSP.")
        return 0
    except (OSError,ValueError,subprocess.SubprocessError) as exc:
        print("No se modificaron controladores USB:",str(exc))
        return 1


if __name__=="__main__":
    raise SystemExit(main())

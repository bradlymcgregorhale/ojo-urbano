#!/usr/bin/env python3
"""Candado singleton para el modelo de visión (CLIP + DINOv2 + SigLIP2).

El stack pesa ~4-5 GB en memoria. Si dos procesos lo cargan a la vez (el
servidor + un script de prueba, o dos servidores en una carrera de reinicio),
la Mac entra en swap y se cuelga. Este candado garantiza UN solo portador del
modelo a la vez: quien va a cargar el modelo llama `adquirir_singleton()`
ANTES de cargarlo; si otro proceso ya lo tiene, lo termina y toma su lugar
("el último que arranca gana"), que es justo lo que se pide — matar cualquier
resto antes de arrancar uno nuevo.

Mecanismo: flock exclusivo (lo libera el kernel si el proceso muere, así un
candado viejo de un proceso caído no bloquea) + un PID escrito en el archivo
para poder terminar al portador de forma dirigida.

Uso:
    import guard_modelo
    guard_modelo.adquirir_singleton()   # antes de cargar torch/joblib
"""
import fcntl
import os
import signal
import subprocess
import sys
import time

LOCK_PATH = os.environ.get("OJO_MODELO_LOCK", "/tmp/ojo_modelo.lock")
_fh = None


def _log(msg):
    sys.stderr.write(f"[guard_modelo] {msg}\n")
    sys.stderr.flush()


def _arranque(pid):
    """Marca de identidad del proceso: su hora de arranque (lstart de ps).

    Sirve para no matar un PID REUSADO: si el PID que guardamos murió y el SO
    reasignó ese número a un proceso ajeno, su hora de arranque no coincide y
    no lo tocamos (hallazgo de codex). Devuelve None si el proceso no existe.
    """
    if pid <= 0:
        return None
    try:
        out = subprocess.run(["ps", "-o", "lstart=", "-p", str(pid)],
                             capture_output=True, text=True, timeout=5)
        s = out.stdout.strip()
        return s or None
    except Exception:
        return None


def _vivo(pid):
    return pid > 0 and pid != os.getpid() and _arranque(pid) is not None


def _terminar(pid, arranque_esperado, espera=8.0):
    """SIGTERM y, tras la gracia, SIGKILL — pero SOLO si el PID sigue siendo el
    MISMO proceso (misma hora de arranque). Si el PID fue reusado, no lo toca."""
    if _arranque(pid) != arranque_esperado:
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    t0 = time.monotonic()
    while _arranque(pid) == arranque_esperado:
        if time.monotonic() - t0 > espera:
            # re-verificar identidad justo antes del SIGKILL (anti-reuso)
            if _arranque(pid) == arranque_esperado:
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            break
        time.sleep(0.2)


def adquirir_singleton(matar=True, espera_total=40.0):
    """Toma el candado del modelo; devuelve tras ser el único portador.

    matar=True (default): termina a cualquier otro portador vivo y toma su
    lugar. matar=False: NO mata a nadie; si el candado está tomado, sale del
    proceso con código 1 (para scripts que prefieren ceder ante el servidor).
    """
    global _fh
    if _fh is not None:
        return  # ya lo tenemos en este proceso
    # NO se libera el flock por atexit: se mantiene tomado por el fd abierto
    # durante toda la vida del proceso, y el kernel lo suelta al morir el
    # proceso, DESPUÉS de que se libere la memoria del modelo. Soltarlo en
    # atexit podía dejar entrar a otro portador mientras nuestros tensores aún
    # están residentes (hallazgo de codex).
    _fh = open(LOCK_PATH, "a+")
    t0 = time.monotonic()
    aviso = False
    while True:
        try:
            fcntl.flock(_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            _fh.seek(0)
            _fh.truncate()
            _fh.write(f"{os.getpid()} {_arranque(os.getpid()) or ''}")
            _fh.flush()
            return
        except BlockingIOError:
            _fh.seek(0)
            partes = (_fh.read() or "").strip().split(" ", 1)
            try:
                pid = int(partes[0] or "0")
            except ValueError:
                pid = 0
            arr = partes[1] if len(partes) > 1 else ""
            vivo = pid > 0 and pid != os.getpid() and _arranque(pid) == arr and arr
            if not vivo:
                # candado huérfano (portador muerto, o PID reusado por un
                # proceso ajeno): el kernel ya lo va a soltar. Reintentar corto.
                time.sleep(0.3)
            elif matar:
                if not aviso:
                    _log(f"otro proceso ({pid}) tiene el modelo; lo termino y tomo su lugar")
                    aviso = True
                _terminar(pid, arr)
                time.sleep(0.3)
            else:
                _log(f"el modelo ya está cargado por el proceso {pid}; "
                     f"usá el servidor en :8080 o cerralo primero. Salgo.")
                sys.exit(1)
            if time.monotonic() - t0 > espera_total:
                _log("no pude adquirir el candado del modelo a tiempo. Salgo.")
                sys.exit(1)


def liberar():
    global _fh
    if _fh is None:
        return
    try:
        fcntl.flock(_fh, fcntl.LOCK_UN)
        _fh.close()
    except Exception:
        pass
    _fh = None


if __name__ == "__main__":
    # modo diagnóstico: adquiere, espera, libera
    adquirir_singleton()
    _log(f"candado tomado por pid {os.getpid()}; ctrl-c para soltar")
    try:
        time.sleep(float(sys.argv[1]) if len(sys.argv) > 1 else 3600)
    except KeyboardInterrupt:
        pass

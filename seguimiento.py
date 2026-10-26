"""Seguimiento diario de repos TREND+TECH (G18/G20).
Lee la hoja "Repositorios", clona cada repo (solo lectura), evalúa los issues 01-18,
sube estados (nunca los baja) y envía un resumen por correo.
Variables de entorno: GCP_SA_JSON, SHEET_ID, GMAIL_USER, GMAIL_APP_PASSWORD, MAIL_TO.
Uso: python seguimiento.py [--dry-run]
"""
import os, re, sys, json, subprocess, tempfile, smtplib, datetime
from email.mime.text import MIMEText
import gspread
from google.oauth2.service_account import Credentials

DRY = '--dry-run' in sys.argv
PESTANA = 'Repositorios'
FILA_ENCABEZADO = 3
ORDEN = {'Pendiente': 0, 'En progreso': 1, 'Hecho': 2}
IMG = ('.png', '.jpg', '.jpeg', '.gif', '.svg', '.webp', '.drawio', '.pdf', '.mp4', '.mov')
# issue -> número de docs/NN-*.md
MAPA = {'01': 1, '02': 2, '04': 3, '05': 4, '06': 4, '07': 5, '08': 5, '09': 6,
        '12': 9, '13': 10, '14': 10, '15': 8, '17': 11}
CALENDARIO = [
    (datetime.date(2026, 10, 25), 'Vencido 25-oct: 05 Entrevista, 06 Mapa de empatía'),
    (datetime.date(2026, 11, 1), 'Semana 26-oct: 07 Hipótesis, 10 Prototipo v0.1 (tag v0.1), solicitudes FabLab'),
    (datetime.date(2026, 11, 8), 'Semana 2-nov: 11 v0.5, 14 FabLab, 15 Validación'),
    (datetime.date(2026, 11, 15), 'Semana 10-nov: 16 v1.0 + videos, 18 Pitch, pre-sustentación'),
    (datetime.date(2026, 11, 18), 'Sustentación 13-18 nov (18-nov)'),
]


# ---------- evaluación de un repo ----------
def palabras(t):
    return sum(len(l.split()) for l in t.splitlines() if l.strip() and not l.strip().startswith('#'))


def tiene_ref(t):
    return bool(re.search(r'!\[[^\]]*\]\(|<img|\.(png|jpe?g|gif|svg|drawio|pdf|mp4)\b|youtu|drive\.google', t, re.I))


def leer(p):
    return open(p, errors='ignore').read()


def evaluar(repo):
    archivos = [os.path.join(r, f) for r, _, fs in os.walk(repo) if '.git' not in r.split(os.sep) for f in fs]
    docs = {}
    for p in archivos:
        if os.path.basename(os.path.dirname(p)).lower() == 'docs' and p.lower().endswith('.md'):
            m = re.match(r'#?0?(\d{1,2})\D', os.path.basename(p))
            if m:
                t, n = leer(p), int(m.group(1))
                if n not in docs or palabras(t) > palabras(docs[n]):
                    docs[n] = t
    out = {}
    for iss, n in MAPA.items():
        t = docs.get(n, '')
        w, ref = palabras(t), tiene_ref(t)
        out[iss] = 'Hecho' if w >= 40 and ref else ('En progreso' if w > 0 or ref else 'Pendiente')
    low = [p.lower() for p in archivos if not p.lower().endswith('.md') or palabras(leer(p)) >= 40]
    out['03'] = 'Hecho' if any('bpmn' in p and 'pract' in p and p.endswith(IMG) for p in low) else 'Pendiente'
    tags = subprocess.run(['git', '-C', repo, 'tag'], capture_output=True, text=True).stdout.lower()
    for iss, v in (('10', '0.1'), ('11', '0.5'), ('16', '1.0')):
        if 'v' + v in tags:
            out[iss] = 'Hecho'
        elif any(('v' + v) in p.replace(' ', '') and p.endswith(IMG) for p in low):
            out[iss] = 'En progreso'
        else:
            out[iss] = 'Pendiente'
    pitch = [p for p in low if '/pitch/' in p and 'plantilla' not in p]
    out['18'] = 'Hecho' if any(p.endswith(('.pptx', '.pdf')) for p in pitch) else ('En progreso' if pitch else 'Pendiente')
    return out


def ultimo_commit(repo):
    r = subprocess.run(['git', '-C', repo, 'log', '-1', '--format=%cd', '--date=short'], capture_output=True, text=True)
    return r.stdout.strip()


# ---------- resumen por correo ----------
# fecha límite de cada issue (fin de su semana); los no listados no tienen fecha fija
LIMITE = {'05': datetime.date(2026, 10, 25), '06': datetime.date(2026, 10, 25),
          '07': datetime.date(2026, 11, 1), '10': datetime.date(2026, 11, 1),
          '11': datetime.date(2026, 11, 8), '14': datetime.date(2026, 11, 8), '15': datetime.date(2026, 11, 8),
          '16': datetime.date(2026, 11, 15), '18': datetime.date(2026, 11, 15)}
SUSTENTACION = datetime.date(2026, 11, 18)


def armar_resumen(hoy, equipos, lineas, alertas, sheet_id, nombres):
    td = 'style="border:1px solid #ccc;padding:4px 8px;font-size:13px"'
    th = 'style="border:1px solid #ccc;padding:4px 8px;font-size:13px;background:#f0f0f0;text-align:left"'
    lunes = hoy - datetime.timedelta(days=hoy.weekday())
    domingo = lunes + datetime.timedelta(days=6)
    semana = sorted(i for i, f in LIMITE.items() if lunes <= f <= domingo)
    filas, sin_actividad = [], []
    for e in sorted(equipos, key=lambda x: (x['grupo'], -sum(v == 'Hecho' for v in x['final'].values()))):
        f = e['final']
        hechos = sum(v == 'Hecho' for v in f.values())
        prog = sum(v == 'En progreso' for v in f.values())
        vencidos = [i for i, d in LIMITE.items() if d < hoy and f.get(i) != 'Hecho']
        esta = [i for i in semana if f.get(i) != 'Hecho']
        color = '#c62828' if vencidos else ('#2e7d32' if hechos >= 9 else '#000')
        dias = e['dias']
        if dias is not None and dias > 5:
            sin_actividad.append(e['equipo'])
        filas.append(
            f'<tr><td {td}>{e["grupo"]}</td><td {td}><b>{e["equipo"]}</b></td>'
            f'<td {td}>{round(hechos * 100 / 18)}%</td><td {td}>{hechos}</td><td {td}>{prog}</td>'
            f'<td {td}>{e["fecha"]}' + (f' ({dias} d)' if dias is not None else '') + '</td>'
            f'<td {td[:-1]};color:{color};font-weight:bold">{", ".join(sorted(vencidos)) or "—"}</td>'
            f'<td {td}>{", ".join(esta) or "—"}</td></tr>')
    total = len(equipos) or 1
    prom = round(sum(sum(v == 'Hecho' for v in e['final'].values()) for e in equipos) * 100 / (18 * total))
    al_dia = sum(1 for e in equipos if not [i for i, d in LIMITE.items() if d < hoy and e['final'].get(i) != 'Hecho'])
    leyenda = ' · '.join(f'{i} = {nombres[i][3:]}' for i in sorted(LIMITE))
    return f'''<div style="font-family:Arial,sans-serif;font-size:14px">
<h2 style="margin:0">Seguimiento TREND+TECH — {hoy.strftime('%d/%m/%Y')}</h2>
<p><b>{(SUSTENTACION - hoy).days} días</b> para la sustentación (18-nov) · Avance promedio: <b>{prom}%</b> ·
Equipos al día con lo vencido: <b>{al_dia}/{len(equipos)}</b>{' · <b style="color:#c62828">MODO PRUEBA</b>' if DRY else ''}</p>
<h3>Cambios detectados hoy</h3><p>{'<br>'.join(lineas) or 'Ningún equipo subió estados desde la última corrida.'}</p>
<h3>Estado por equipo</h3>
<table style="border-collapse:collapse">
<tr><th {th}>Grupo</th><th {th}>Equipo</th><th {th}>Avance</th><th {th}>Hecho</th><th {th}>En progreso</th>
<th {th}>Último commit</th><th {th}>Vencidos sin hacer</th><th {th}>Vence esta semana</th></tr>
{''.join(filas)}</table>
<p style="font-size:12px;color:#555">{leyenda}</p>
<h3>Alertas</h3><p>{'<br>'.join(alertas + [f'{n}: sin commits hace más de 5 días' for n in sin_actividad if not any(n in a for a in alertas)]) or 'Ninguna.'}</p>
<p><a href="https://docs.google.com/spreadsheets/d/{sheet_id}/edit">Abrir hoja de seguimiento</a></p></div>'''


# ---------- utilidades de hoja ----------
def col_letra(i):  # 0 -> A
    s = ''
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def main():
    creds = Credentials.from_service_account_info(json.loads(os.environ['GCP_SA_JSON']),
                                                  scopes=['https://www.googleapis.com/auth/spreadsheets'])
    hoja = gspread.authorize(creds).open_by_key(os.environ['SHEET_ID']).worksheet(PESTANA)
    datos = hoja.get_all_values()
    enc = datos[FILA_ENCABEZADO - 1]

    # columnas por encabezado (nunca por letra fija)
    if enc.count('Repositorio') != 1:
        sys.exit('ERROR: no hay exactamente una columna "Repositorio". No se escribe nada.')
    c_repo = enc.index('Repositorio')
    c_iss = {}
    for i, h in enumerate(enc):
        m = re.match(r'^(\d{2}) ', h.strip())
        if m:
            if m.group(1) in c_iss:
                sys.exit(f'ERROR: encabezado de issue {m.group(1)} duplicado. No se escribe nada.')
            c_iss[m.group(1)] = i
    faltan = [f'{n:02d}' for n in range(1, 19) if f'{n:02d}' not in c_iss]
    if faltan:
        sys.exit(f'ERROR: faltan encabezados de issues {faltan}. No se escribe nada.')

    hoy = datetime.date.today()
    cambios, lineas, alertas, equipos = [], [], [], []
    c_grupo = enc.index('Grupo (G18/G20)') if 'Grupo (G18/G20)' in enc else 1
    c_nombre = {iss: enc[c] for iss, c in c_iss.items()}
    tmp = tempfile.mkdtemp()
    for r in range(FILA_ENCABEZADO, len(datos)):
        fila = datos[r]
        equipo = fila[0].strip() if fila else ''
        if not equipo:
            continue
        url = (fila[c_repo] if c_repo < len(fila) else '').strip().rstrip('/').removesuffix('.git')
        if not url.startswith('https://github.com/'):
            alertas.append(f'{equipo}: URL de repo inválida, omitido')
            continue
        d = os.path.join(tmp, str(r))
        res = subprocess.run(['git', 'clone', '-q', '--depth', '30', url, d], capture_output=True, text=True,
                             env={**os.environ, 'GIT_LFS_SKIP_SMUDGE': '1', 'GIT_TERMINAL_PROMPT': '0'})
        if res.returncode != 0:
            alertas.append(f'{equipo}: no se pudo clonar')
            continue
        est = evaluar(d)
        fecha = ultimo_commit(d)
        if fecha and (hoy - datetime.date.fromisoformat(fecha)).days > 5:
            alertas.append(f'{equipo}: sin commits desde {fecha}')
        subidas, final = [], {}
        for iss, c in sorted(c_iss.items()):
            actual = fila[c].strip() if c < len(fila) else ''
            nuevo = est[iss]
            final[iss] = actual if actual in ORDEN else 'Pendiente'
            if ORDEN[nuevo] > ORDEN.get(actual, 0):  # regla de no retroceso
                cambios.append({'range': f'{PESTANA}!{col_letra(c)}{r + 1}', 'values': [[nuevo]]})
                subidas.append(f'{c_nombre[iss]}: {actual or "vacío"} → {nuevo}')
                final[iss] = nuevo
        if subidas:
            lineas.append(f'{equipo}: ' + '; '.join(subidas))
        equipos.append({
            'equipo': equipo, 'grupo': fila[c_grupo] if c_grupo < len(fila) else '',
            'final': final, 'fecha': fecha or '—',
            'dias': (hoy - datetime.date.fromisoformat(fecha)).days if fecha else None,
        })

    if cambios and not DRY:
        hoja.spreadsheet.values_batch_update({'valueInputOption': 'USER_ENTERED', 'data': cambios})

    cuerpo = armar_resumen(hoy, equipos, lineas, alertas, os.environ['SHEET_ID'], c_nombre)
    print(re.sub(r'<[^>]+>', ' ', cuerpo))

    if os.environ.get('GMAIL_APP_PASSWORD'):
        msg = MIMEText(cuerpo, 'html', 'utf-8')
        msg['Subject'] = f'Seguimiento TREND+TECH {hoy.isoformat()}'
        msg['From'] = os.environ['GMAIL_USER']
        msg['To'] = os.environ['MAIL_TO']
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as s:
            s.login(os.environ['GMAIL_USER'], os.environ['GMAIL_APP_PASSWORD'])
            s.send_message(msg)
        print('Correo enviado a', os.environ['MAIL_TO'])


if __name__ == '__main__':
    main()

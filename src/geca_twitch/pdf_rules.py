"""Reglas puras, auditables, de GECA. Sin efectos secundarios sobre SQLite.
El criterio de una captura a las 23:00 UTC +/- 90 min es una DECISIÓN OPERATIVA,
no una hora exigida por el PDF. Mantener alcance comparable al aplicarlo.
"""
from dataclasses import dataclass, asdict
from datetime import date, datetime, time, timedelta, timezone
from typing import Optional

UTC=timezone.utc

@dataclass(frozen=True)
class Resultado:
    valido: bool
    alerta: bool
    motivo: str
    modo: str
    dias_observados: int
    fechas: tuple[str,...]
    promedio_anterior: Optional[float]=None
    promedio_reciente: Optional[float]=None
    incremento: Optional[float]=None
    crecimiento_pct: Optional[float]=None
    huecos: int=0


def fecha_utc(value):
    if isinstance(value, datetime):
        if value.tzinfo is None: raise ValueError('Timestamp sin zona UTC')
        return value.astimezone(UTC)
    stamp=datetime.fromisoformat(str(value).replace('Z','+00:00'))
    if stamp.tzinfo is None: raise ValueError('Timestamp sin zona UTC')
    return stamp.astimezone(UTC)


def steam_proximo(coming_soon, ficha_estado, verificado_utc, corte:date,
                  lanzamiento=None):
    """PDF: por salir y ficha validada dentro de 7 días (incluyendo el corte).
    Fechas indeterminadas NO se inventan, pero un coming_soon explícito sí cuenta.
    """
    if coming_soon is not True: return False,'no_confirmado_proximo'
    if str(ficha_estado).lower()!='ok': return False,'ficha_no_validada'
    try:
        verificado=fecha_utc(verificado_utc).date()
    except (ValueError, TypeError):return False,'fecha_verificacion_invalida'
    antiguedad=(corte-verificado).days
    if not 0<=antiguedad<=7:return False,'verificacion_fuera_de_7_dias'
    if lanzamiento:
        try:
            fecha=date.fromisoformat(str(lanzamiento)[:10])
        except ValueError:fecha=None
        if fecha is not None and fecha<corte:return False,'ya_lanzado'
    return True,'proximo_verificado'


def comparar_atencion(observaciones, corte:date, modo='oficial', dias_experimentales=2,
                       hora_objetivo=23, tolerancia_min=90):
    """Observaciones: {captured_utc,viewers_observed,streams_complete,scope?}.
    Oficial: dos semanas de siete días, consecutivas y anteriores al corte.
    Experimental: N fechas válidas no necesariamente consecutivas durante nov-dic 2026.
    La ventana es por juego; nunca confunde cero con ausencia.
    """
    if modo not in ('oficial','experimental'):raise ValueError('Modo inválido')
    if modo=='experimental' and not (date(2026,11,1)<=corte<=date(2026,12,31)):
        return Resultado(False,False,'fuera_excepcion_2026',modo,0,())
    n=14 if modo=='oficial' else dias_experimentales
    if modo=='experimental' and not 2<=n<=60:raise ValueError('N debe estar entre 2 y 60')
    end=corte-timedelta(days=1)
    days=([end-timedelta(days=i) for i in range(n-1,-1,-1)] if modo=='oficial'
          else sorted({(date.fromisoformat(o['session_day']) if o.get('session_day') else fecha_utc(o['captured_utc']).date()) for o in observaciones
                       if (date.fromisoformat(o['session_day']) if o.get('session_day') else fecha_utc(o['captured_utc']).date())<corte})[-n:])
    by_day={}
    for d in days:
        target=datetime.combine(d,time(hora_objetivo),tzinfo=UTC)
        valid=[]
        for o in observaciones:
            try:
                timestamp=fecha_utc(o['captured_utc'])
                if o.get('session_day') and o['session_day'] != d.isoformat():continue
                diff=abs((timestamp-target).total_seconds()/60)
                if diff<=tolerancia_min and int(o['streams_complete'])==1:
                    valid.append((diff,timestamp,str(o.get('scope','default')),
                                  float(o['viewers_observed'])))
            except (ValueError,TypeError,KeyError):continue
        if valid:
            by_day[d]=min(valid,key=lambda x:(x[0],x[1]))
    selected=sorted(by_day)
    fechas=tuple(d.isoformat() for d in selected)
    huecos=((selected[-1]-selected[0]).days+1-len(selected)) if selected else 0
    if len(selected)<n:
        return Resultado(False,False,'faltan_dias_o_capturas_completas',modo,len(selected),fechas,huecos=huecos)
    offsets=[(by_day[d][1]-datetime.combine(d,time(hora_objetivo),tzinfo=UTC)).total_seconds()/60
             for d in selected]
    if max(offsets)-min(offsets)>tolerancia_min:
        return Resultado(False,False,'hora_no_comparable',modo,n,fechas,huecos=huecos)
    if len({by_day[d][2] for d in selected})!=1:
        return Resultado(False,False,'alcance_no_comparable',modo,n,fechas,huecos=huecos)
    vals=[by_day[d][3] for d in selected]
    split=7 if modo=='oficial' else n//2
    anterior=sum(vals[:split])/split
    reciente=sum(vals[split:])/(n-split)
    if anterior<=0:
        return Resultado(False,False,'promedio_anterior_cero',modo,n,fechas,
                         anterior,reciente,huecos=huecos)
    incremento=reciente-anterior
    crecimiento=100*incremento/anterior
    alerta=incremento>=500 and crecimiento>=50
    return Resultado(True,alerta,'alerta' if alerta else 'umbrales_no_cumplidos',modo,n,
                     fechas,anterior,reciente,incremento,crecimiento,huecos)


def propuestas(senales, maximo=3):
    """Toda señal se retiene, pero el resumen devuelve como máximo tres juegos.
    No convierte el ranking técnico en decisión sobre derechos/encaje editorial.
    """
    por_juego={}
    for s in senales:
        clave=str(s['game_key'])
        slot=por_juego.setdefault(clave,{'game_key':clave,'game_name':s['game_name'],
                                       'reglas':[],'evidencias':[], 'pregunta_geca':
                                       '¿Por qué merece análisis editorial ahora?'})
        slot['reglas'].append(s['rule']);slot['evidencias'].append(s['evidence'])
    result=list(por_juego.values())
    result.sort(key=lambda r:(-len(set(r['reglas'])),r['game_key']))
    return result[:maximo]

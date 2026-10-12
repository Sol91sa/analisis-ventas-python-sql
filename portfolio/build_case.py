"""Reproduce the UCI Online Retail portfolio case. Run from any directory."""
from pathlib import Path
import argparse
import hashlib
import json
import math
import sqlite3
import urllib.request
import zipfile

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / 'data' / 'portfolio_raw'
OUT = Path(__file__).resolve().parent / 'results'
URL = 'https://archive.ics.uci.edu/static/public/352/online%2Bretail.zip'
SOURCE = 'https://archive.ics.uci.edu/dataset/352/online+retail'
FX_FILE = Path(__file__).parent / 'sources' / 'bcra_2026-10-09.json'
FX_URL = 'https://api.bcra.gob.ar/estadisticascambiarias/v1.0/Cotizaciones?fecha=2026-10-09'


def load_fx():
    """Read a frozen official quote; never silently use a changing live rate."""
    payload = FX_FILE.read_bytes()
    source = json.loads(payload)
    if source['status'] != 200 or source['results']['fecha'] != '2026-10-09':
        raise ValueError('Unexpected BCRA response or quotation date.')
    rows = [row for row in source['results']['detalle'] if row['codigoMoneda'] == 'GBP']
    if len(rows) != 1 or not np.isfinite(rows[0]['tipoCotizacion']) or rows[0]['tipoCotizacion'] <= 0:
        raise ValueError('Expected one positive finite GBP/ARS quote.')
    return dict(base_currency='GBP', presentation_currency='ARS',
                ars_per_gbp=rows[0]['tipoCotizacion'], date=source['results']['fecha'],
                source_url=FX_URL, source_sha256=hashlib.sha256(payload).hexdigest(),
                source_field='results.detalle[codigoMoneda=GBP].tipoCotizacion',
                method='Fixed presentation rate, not transaction-date conversion or inflation adjustment.')


def convert_money(stats, orders, monthly, customers, quality):
    """Keep GBP evidence and add ARS amounts before display-only rounding."""
    fx = load_fx()
    rate = fx['ars_per_gbp']
    stats = dict(stats, fx=fx)
    for key, value in list(stats.items()):
        if key.endswith('_gbp'):
            stats[key[:-4] + '_ars'] = value * rate
    orders = orders.rename(columns={'importe': 'importe_gbp'}).copy()
    orders['importe'] = orders['importe_gbp'] * rate
    orders['moneda'] = 'ARS'
    orders['tipo_cambio_ars_por_gbp'] = rate
    orders['fecha_tipo_cambio'] = fx['date']
    monthly = monthly.rename(columns={'gross_sales': 'gross_sales_gbp', 'average_invoice': 'average_invoice_gbp'}).copy()
    customers = customers.rename(columns={'gross_sales': 'gross_sales_gbp'}).copy()
    quality = quality.copy()
    for table in [monthly, customers, quality]:
        for column in list(table.columns):
            if column.endswith('_gbp'):
                table[column[:-4] + '_ars'] = table[column] * rate
    np.testing.assert_allclose(orders['importe'].sum(), stats['gross_sales_ars'], rtol=0, atol=.01)
    np.testing.assert_allclose(monthly['gross_sales_ars'].sum(), stats['gross_sales_ars'], rtol=0, atol=.01)
    return stats, orders, monthly, customers, quality


def number_es(value, decimals=2):
    return f'{value:,.{decimals}f}'.translate(str.maketrans(',.', '.,'))


def pesos(value):
    return '$' + number_es(value) + ' ARS'


def prepare(frame):
    """Keep original rows and add explicit, mutually exclusive classifications."""
    frame = frame.copy()
    for col in ['InvoiceNo', 'StockCode', 'CustomerID']:
        frame[col] = frame[col].astype('string').str.strip()
    frame['CustomerID'] = frame['CustomerID'].str.replace(r'\.0$', '', regex=True)
    frame['InvoiceDate'] = pd.to_datetime(frame['InvoiceDate'], errors='raise')
    frame['amount'] = frame['Quantity'] * frame['UnitPrice']
    product = frame['StockCode'].str.upper().str.fullmatch(r'\d{5}[A-Z]*').fillna(False)
    valid = (frame['InvoiceDate'].notna() & frame['InvoiceNo'].notna()
             & np.isfinite(frame['Quantity']) & np.isfinite(frame['UnitPrice']))
    credit = frame['InvoiceNo'].str.upper().str.startswith('C').fillna(False)
    conditions = [~valid, ~product, frame['UnitPrice'].le(0), credit & frame['Quantity'].lt(0),
                  credit, frame['Quantity'].le(0)]
    frame['classification'] = np.select(
        [condition.to_numpy(dtype=bool, na_value=False) for condition in conditions],
        ['invalid', 'non_product_code', 'non_positive_price', 'credit',
         'other_cancellation', 'non_positive_quantity'], default='sale')
    return frame


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--download', action='store_true', help='Download only if the archive is absent.')
    args = parser.parse_args()
    RAW.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)
    archive = RAW / 'online_retail.zip'
    if not archive.exists():
        if not args.download:
            raise SystemExit('Run with --download or place online_retail.zip in data/portfolio_raw/.')
        with urllib.request.urlopen(URL, timeout=90) as response:
            payload = response.read()
        temporary = archive.with_suffix('.tmp')
        temporary.write_bytes(payload)
        temporary.replace(archive)
    with zipfile.ZipFile(archive) as zipped:
        with zipped.open('Online Retail.xlsx') as handle:
            original = pd.read_excel(handle, dtype={'InvoiceNo': 'string', 'StockCode': 'string', 'CustomerID': 'string'})
    print(f'Read {len(original):,} source lines.', flush=True)
    frame = prepare(original)
    db = sqlite3.connect(':memory:')
    frame.to_sql('transactions', db, index=False)
    sql = (Path(__file__).parent / 'analysis.sql').read_text()
    db.executescript(sql)
    orders = pd.read_sql_query('SELECT * FROM orders ORDER BY fecha, pedido', db)
    monthly = pd.read_sql_query('SELECT * FROM monthly ORDER BY month', db)
    customers = pd.read_sql_query('SELECT * FROM customers ORDER BY gross_sales DESC, customer', db)
    if orders.empty or customers.empty:
        raise ValueError('No qualifying sales/customers.')
    sales = frame.loc[frame['classification'].eq('sale')].copy()
    gross = float(sales['amount'].sum())
    # Independent pandas/NumPy reconciliation of SQL outputs.
    np.testing.assert_allclose(orders['importe'].sum(), gross, rtol=0, atol=.0001)
    np.testing.assert_allclose(monthly['gross_sales'].sum(), gross, rtol=0, atol=.0001)
    np.testing.assert_allclose(customers['gross_sales'].sum(), sales.loc[sales['CustomerID'].notna(), 'amount'].sum(), rtol=0, atol=.0001)
    if len(orders) != sales['InvoiceNo'].nunique():
        raise AssertionError('Invoice count mismatch.')
    conflicts = sales.groupby('InvoiceNo').agg(clients=('CustomerID', 'nunique'), dates=('InvoiceDate', lambda x: x.dt.date.nunique()))
    if (conflicts[['clients', 'dates']] > 1).any().any():
        raise ValueError('An invoice has conflicting customers or dates.')
    count_top = math.ceil(len(customers) * .05)
    known_gross = float(customers['gross_sales'].sum())
    dedup = prepare(original.drop_duplicates())
    dedup_gross = float(dedup.loc[dedup['classification'].eq('sale'), 'amount'].sum())
    credits = -float(frame.loc[frame['classification'].eq('credit'), 'amount'].sum())
    stats = dict(
        source=SOURCE, citation='Chen, D. (2015). Online Retail. UCI Machine Learning Repository. DOI: 10.24432/C5BW33.',
        license='CC BY 4.0', archive_sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
        source_rows=len(frame), date_min=str(frame.InvoiceDate.min()), date_max=str(frame.InvoiceDate.max()),
        exact_duplicate_extra_rows=int(original.duplicated().sum()),
        missing_customer_rows=int(frame.CustomerID.isna().sum()),
        gross_sales_gbp=gross, gross_without_exact_duplicates_gbp=dedup_gross,
        duplicate_sensitivity_gbp=gross-dedup_gross,
        sale_lines=len(sales), invoices=len(orders), known_customers=len(customers),
        known_customer_sales_gbp=known_gross,
        unidentified_sales_share_pct=100*(gross-known_gross)/gross,
        top_5pct_customers=count_top,
        top_5pct_known_sales_share_pct=100*float(customers.head(count_top).gross_sales.sum())/known_gross,
        credit_amount_gbp=credits,
        credit_to_positive_sales_pct=100*credits/gross,
        gross_less_recorded_credits_gbp=gross-credits,
        average_invoice_gbp=gross/len(orders),
        excluded_lines=len(frame)-len(sales),
        reconciliation='SQL totals independently reconciled against pandas and NumPy; invoice consistency checked.',
    )
    quality = frame.groupby('classification', dropna=False).agg(rows=('amount', 'size'), signed_amount_gbp=('amount', 'sum')).reset_index()
    stats, orders, monthly, customers, quality = convert_money(stats, orders, monthly, customers, quality)
    for name, table in [('orders_for_app', orders), ('monthly', monthly), ('customer_concentration', customers), ('quality', quality)]:
        table.to_csv(OUT / f'{name}.csv', index=False)
    (OUT / 'metrics.json').write_text(json.dumps(stats, indent=2, ensure_ascii=False) + '\n')
    draw_charts(stats, monthly, customers)
    write_story(stats)
    print(json.dumps(stats, indent=2, ensure_ascii=False))


def draw_charts(s, monthly, customers):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.ticker import FuncFormatter
    import seaborn as sns
    sns.set_theme(style='whitegrid', font='DejaVu Sans', context='notebook')
    plt.rcParams.update({'axes.spines.top': False, 'axes.spines.right': False})
    fig, axes = plt.subplots(2, 2, figsize=(15, 10), facecolor='#f7f9fc')
    fig.subplots_adjust(top=.77, bottom=.12, left=.08, right=.96, hspace=.62, wspace=.30)
    fig.text(.08, .95, 'Sales & Data Quality Analytics', fontsize=26, weight='bold', color='#152c48')
    fig.text(.08, .906, 'UCI Online Retail · UK transactions, Dec 2010–Dec 2011 · Presentation currency: Argentine pesos (ARS)', fontsize=11, color='#526176')
    fig.text(.08, .878, f"BCRA · 09/10/2026 · 1 GBP = ${number_es(s['fx']['ars_per_gbp'], 4)} ARS · Fixed conversion; no inflation adjustment", fontsize=11, color='#526176')
    fig.text(.08, .838, f"${number_es(s['gross_sales_ars']/1e6)} M ARS positive sales     {s['invoices']:,} invoices     {s['known_customers']:,} identified customers", fontsize=14, weight='bold', color='#087f8c')
    ax = axes[0, 0]
    monthly = monthly.copy()
    labels = pd.to_datetime(monthly['month']).dt.strftime('%b %y')
    colors = ['#087f8c'] * len(monthly)
    colors[-1] = '#c2cad5'
    ax.bar(labels, monthly['gross_sales_ars'], color=colors, width=.7)
    ax.set_title('Sales trend: December 2011 is incomplete', loc='left', weight='bold', pad=14)
    ax.tick_params(axis='x', rotation=55, labelsize=9)
    ax.yaxis.set_major_formatter(FuncFormatter(lambda x, _: f'${number_es(x/1e6, 0)}'))
    ax.set_ylabel('Positive merchandise sales · million ARS')
    ax = axes[0, 1]
    x = np.arange(1, len(customers)+1)/len(customers)*100
    y = customers.gross_sales_ars.cumsum()/customers.gross_sales_ars.sum()*100
    ax.plot(np.r_[0, x], np.r_[0, y], color='#087f8c', linewidth=3)
    ax.plot([0, 100], [0, 100], color='#b6bfcc', linestyle='--', linewidth=1)
    ax.scatter([x[s['top_5pct_customers']-1]], [s['top_5pct_known_sales_share_pct']], color='#e48a35', s=70, zorder=3)
    ax.text(16, 40, f"Top 5% of identified customers\n{s['top_5pct_known_sales_share_pct']:.1f}% of identified sales", fontsize=11)
    ax.set(xlim=(0, 100), ylim=(0, 103), xlabel='Cumulative customers (%) · largest first', ylabel='Cumulative identified sales (%)')
    ax.set_title('Revenue concentration', loc='left', weight='bold', pad=14)
    ax = axes[1, 0]
    missing = s['unidentified_sales_share_pct']
    ax.barh(['Customer identification'], [100-missing], color='#087f8c', label='Identified', height=.35)
    ax.barh(['Customer identification'], [missing], left=[100-missing], color='#e48a35', label='Missing customer ID', height=.35)
    ax.set(xlim=(0, 100), xlabel='Share of positive merchandise sales (%)')
    ax.set_ylim(-.65, .65)
    ax.set_yticks([])
    ax.set_title(f'{missing:.1f}% of sales cannot be assigned to a customer', loc='left', weight='bold', pad=14, fontsize=11)
    ax.legend(loc='upper center', bbox_to_anchor=(.5, .22), ncol=2, frameon=False, fontsize=10)
    ax = axes[1, 1]
    values = [s['gross_sales_ars'], s['credit_amount_ars'], s['gross_less_recorded_credits_ars']]
    bars = ax.bar(['Positive sales', 'Recorded credits', 'Sales less credits'], values, color=['#087f8c', '#e48a35', '#152c48'])
    for bar, value in zip(bars, values):
        ax.text(bar.get_x()+bar.get_width()/2, value+max(values)*.025, f'${number_es(value/1e6, 0)} M', ha='center', fontsize=10)
    ax.set_ylim(0, max(values)*1.22)
    ax.yaxis.set_major_formatter(FuncFormatter(lambda x, _: f'${number_es(x/1e6, 0)}'))
    ax.set_ylabel('Million ARS')
    ax.set_title('Credits need a separate reconciliation', loc='left', weight='bold', pad=14)
    ax.tick_params(axis='x', labelsize=10)
    fig.text(.08, .025, 'Source: D. Chen (2015), UCI Online Retail, CC BY 4.0. Product-code filter applied; exact duplicates retained.\nCredits are not matched to original sales. Concentration excludes unidentified customers. No profit or causal impact is estimated.', fontsize=10, color='#526176')
    fig.savefig(OUT / 'portfolio_overview.png', dpi=150, facecolor=fig.get_facecolor())
    plt.close(fig)


def write_story(s):
    report = f'''# ¿Cuánto podemos confiar en los indicadores de ventas?

Caso personal de análisis comercial con Python, pandas, NumPy y SQL. Datos históricos de un comercio online del Reino Unido. Período observado: {s['date_min']} a {s['date_max']}. Moneda de presentación: pesos argentinos ($ ARS). Moneda original: GBP.

## Conversión documentada

Se aplica **1 GBP = ${number_es(s['fx']['ars_per_gbp'], 4)} ARS**, cotización publicada por el **BCRA para el 09/10/2026**, a todos los importes. Fórmula: `importe ARS = importe GBP × {s['fx']['ars_per_gbp']}`. [Respuesta oficial del BCRA]({s['fx']['source_url']}) y [copia conservada](sources/bcra_2026-10-09.json).

Se eligió una tasa fija de presentación cercana a la fecha de preparación del portfolio para facilitar la lectura en pesos. **No representa el tipo de cambio de las ventas de 2010–2011, una actualización por inflación ni ventas de un comercio argentino.** No incluye comisiones ni impuestos cambiarios. La fuente, la fecha y la huella SHA-256 quedan en `results/metrics.json`.

Los cálculos y las exportaciones conservan precisión antes del redondeo. El informe muestra dos decimales y los gráficos millones de ARS. Los valores GBP se conservan junto a los ARS; los porcentajes, rankings y conteos no cambian al aplicar la misma tasa positiva.

![Resumen visual](results/portfolio_overview.png)

## Resultados

Se analizaron **{s['source_rows']:,} líneas** del archivo original. Con la definición documentada de venta de mercadería se obtuvieron **{pesos(s['gross_sales_ars'])} en ventas positivas**, **{s['invoices']:,} facturas** y un importe medio de **{pesos(s['average_invoice_ars'])} por factura incluida**. Una factura puede contener varias líneas. Los totales incluyen únicamente las líneas que cumplen la definición.

1. **Concentración:** los {s['top_5pct_customers']} clientes que componen aproximadamente el 5% superior de los {s['known_customers']:,} clientes identificados acumulan el **{s['top_5pct_known_sales_share_pct']:.1f}%** de las ventas identificadas. Propuesta: revisar continuidad de compra y necesidades de servicio de este grupo antes de diseñar una campaña. No demuestra abandono ni permite estimar su rentabilidad.
2. **Cobertura:** el **{s['unidentified_sales_share_pct']:.1f}%** de las ventas positivas no tiene identificador de cliente. Se conserva en los totales, pero se excluye del ranking. Propuesta: investigar cómo se captura ese dato antes de generalizar conclusiones de segmentación.
3. **Cancelaciones:** las líneas de mercadería con factura C, cantidad negativa y precio positivo suman **{pesos(s['credit_amount_ars'])}** en valor absoluto, equivalente al **{s['credit_to_positive_sales_pct']:.1f}%** de las ventas positivas. Propuesta: conciliar estas notas con sus facturas de origen. El cociente no es una tasa de devolución de pedidos ni una pérdida de beneficios.

## Una decisión de limpieza que cambia el resultado

Hay **{s['exact_duplicate_extra_rows']:,} filas adicionales exactamente repetidas**. Se conservan en el escenario principal porque no existe un identificador único de línea que permita confirmar un error de carga. Quitarlas produciría ventas positivas por **{pesos(s['gross_without_exact_duplicates_ars'])}**, una diferencia de **{pesos(s['duplicate_sensitivity_ars'])}**. La comparación muestra sensibilidad a una regla, no un error confirmado del comercio.

## Método y límites

- Importe de línea = cantidad × precio unitario. Venta positiva = factura sin prefijo C, cantidad y precio positivos, fecha válida y código de producto con cinco dígitos seguidos de cero o más letras. Este filtro operativo separa cargos y códigos administrativos; puede excluir códigos atípicos de mercadería.
- Se conserva el archivo original. Las exclusiones se clasifican con prioridad explícita en `build_case.py` y se cuentan en `results/quality.csv`.
- SQL agrega primero las líneas por factura en GBP. Se verifican consistencia de fecha y cliente, conteo de facturas y totales independientes con pandas/NumPy. Luego se convierten a ARS. Importes analíticos en punto flotante; conciliación con tolerancia de 0,0001 GBP antes de convertir y $0,01 ARS en los totales convertidos.
- No se eliminan grandes compras como atípicos por su tamaño. El comercio incluye compradores mayoristas.
- Diciembre de 2011 termina el día 9; no se compara su total con meses completos para concluir una caída. Un único ciclo anual no prueba estacionalidad recurrente.
- Este caso no evalúa pronósticos: no alcanza los 24 meses requeridos por la app. La función de pronóstico de la aplicación es una capacidad separada.
- No hay costos, margen, inversión publicitaria ni experimento. No se estiman rentabilidad, ROI ni mejoras comerciales realizadas.

## Fuente

[Chen, D. (2015). Online Retail. UCI Machine Learning Repository](https://doi.org/10.24432/C5BW33). Licencia [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). Los resultados son una transformación analítica propia del dataset histórico, no una auditoría del negocio actual.
'''
    (Path(__file__).parent / 'CASO.md').write_text(report)
    linkedin = f'''# Texto para la sección Proyectos de LinkedIn

## Nombre
Sales & Data Quality Analytics — Python y SQL

## Descripción
Proyecto personal de análisis comercial y calidad de datos, acompañado de una aplicación local para cargar, preparar y explorar archivos de ventas.

El caso analiza {s['source_rows']:,} líneas de transacciones públicas de UCI Online Retail con Python, pandas, NumPy y SQL. Examina concentración de clientes, cancelaciones y el efecto de distintas decisiones de limpieza sobre las métricas.

Entre los resultados: aproximadamente el 5% de los clientes identificados concentra el {s['top_5pct_known_sales_share_pct']:.1f}% de las ventas identificadas, mientras que el {s['unidentified_sales_share_pct']:.1f}% de las ventas positivas carece de identificador de cliente. Incluye visualizaciones con Seaborn/Matplotlib, consultas reproducibles, controles de conciliación y recomendaciones con límites explícitos.

Importes presentados en pesos argentinos mediante conversión fija documentada del BCRA: 1 GBP = ${number_es(s['fx']['ars_per_gbp'], 4)} ARS al 09/10/2026. Los datos originales corresponden al Reino Unido en 2010–2011; la conversión no ajusta por inflación.

## English version
Personal sales analytics and data quality project, supported by a local application for importing, preparing and exploring sales files.

Analyzed {s['source_rows']:,} public transaction lines from UCI Online Retail using Python, pandas, NumPy and SQL. Assessed customer concentration, cancellation records and the sensitivity of sales metrics to data-cleaning decisions. The top 5% of identified customers account for {s['top_5pct_known_sales_share_pct']:.1f}% of identified sales; {s['unidentified_sales_share_pct']:.1f}% of positive sales have no customer ID. Includes reproducible queries, reconciliation checks, visualizations and a documented business case.

Amounts are presented in Argentine pesos using a documented fixed BCRA rate of 1 GBP = {s['fx']['ars_per_gbp']:,.4f} ARS dated 9 October 2026. Source transactions are from the UK in 2010–2011; this is a presentation conversion, without inflation adjustment.

## Aptitudes
Python, SQL, pandas, NumPy, visualización de datos, calidad de datos, análisis exploratorio, Streamlit.

## Antes de publicar
- Añadir la URL real del repositorio o de la demostración cuando esté disponible.
- Adjuntar `results/portfolio_overview.png` y revisar que puedas explicar las definiciones y consultas.
- Indicar la contribución personal con precisión. Este caso, su código y sus textos se prepararon con asistencia de Codex; revisar y adaptar las conclusiones antes de presentarlas.
- No atribuir empleo en UCI o en el comercio, ni impacto económico implementado: es un proyecto personal con datos públicos históricos.
'''
    (Path(__file__).parent / 'LINKEDIN.md').write_text(linkedin)


if __name__ == '__main__':
    main()

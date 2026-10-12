# Sales & Data Quality Analytics

Un caso de análisis comercial para acompañar la aplicación local de este repositorio.

- [Caso y recomendaciones](CASO.md)
- [Presentación para LinkedIn en español e inglés](LINKEDIN.md)
- [Consultas SQL](analysis.sql)
- [Resultados numéricos y huella de la fuente](results/metrics.json)
- [Resumen visual](results/portfolio_overview.png)

## Reproducir

Desde la raíz del repositorio, en un entorno Python 3.12 o compatible:

```sh
python -m pip install -r portfolio/requirements.txt
python portfolio/build_case.py --download
```

La descarga es pública y no requiere credenciales. El script conserva el ZIP en `data/portfolio_raw/`, lee el Excel original, clasifica registros, ejecuta SQL en SQLite y verifica totales con pandas/NumPy. Genera las tablas, la imagen, el caso y el texto de LinkedIn. Una nueva ejecución reemplaza estos resultados derivados. La aplicación original no se modifica.

## Explorar el caso en la aplicación

1. Abrir la app con `iniciar.command`.
2. Cargar `portfolio/results/orders_for_app.csv`.
3. Convertir `importe` a número y `fecha` a fecha ISO. `importe` está en pesos argentinos (ARS); `importe_gbp` conserva el original. La fecha de venta y la fecha de cotización son campos diferentes.
4. En ventas, seleccionar `pedido`, `cliente`, `fecha` e `importe`. Cada fila ya representa una factura con sus líneas elegibles sumadas.
5. Analizar ventas y clientes. Los clientes ausentes no deben imputarse como si fueran una persona real. El período no permite usar el módulo de pronósticos.

Las facturas con mezcla de líneas identificadas y no identificadas se exportan con cliente ausente. El caso calcula concentración por cliente en líneas identificadas; si una fuente nueva contiene facturas mixtas, su cobertura puede diferir de la app.

## Moneda y conversión

Los resultados se presentan en **pesos argentinos (ARS)** con una tasa fija de **1 GBP = $2.007,7495 ARS**, publicada por el BCRA para el **09/10/2026**. Se usa `tipoCotizacion` para GBP de la [API oficial](https://api.bcra.gob.ar/estadisticascambiarias/v1.0/Cotizaciones?fecha=2026-10-09). La [respuesta original](sources/bcra_2026-10-09.json) queda guardada: repetir el script no descarga una tasa nueva ni cambia la fecha elegida.

Es una conversión de presentación de transacciones británicas de 2010–2011. No usa la tasa histórica de cada venta, no actualiza por inflación y no representa un comercio argentino. Se mantienen los importes originales en GBP y se añaden columnas con sufijo `_ars`; el archivo para la app usa `importe` en ARS y `importe_gbp` como referencia. Se redondea únicamente al presentar resultados. La tasa, su fecha, la fuente y su huella se incluyen en `results/metrics.json`.

## Definiciones

| Métrica | Definición |
| --- | --- |
| Ventas positivas | Cantidad × precio de líneas elegibles, antes de cancelaciones |
| Facturas | Identificadores de factura distintos con al menos una línea elegible |
| Ticket promedio | Ventas positivas / facturas incluidas |
| Concentración del 5% | Ventas del mayor 5% de clientes, redondeado hacia arriba, / ventas identificadas |
| Ventas sin cliente | Ventas de líneas sin CustomerID / ventas positivas |
| Créditos | Valor absoluto de líneas con factura C, cantidad negativa, precio positivo y código de mercadería |
| Sensibilidad a duplicados | Diferencia al excluir filas idénticas del original; no implica errores confirmados |

El filtro de mercadería acepta cinco dígitos seguidos de letras opcionales. Las categorías de exclusión son mutuamente excluyentes y su prioridad figura en `prepare()`. Se mantienen compras grandes y filas repetidas en el escenario principal. Diciembre de 2011 está incompleto.

## Fuente y atribución

Chen, D. (2015). *Online Retail*. UCI Machine Learning Repository. https://doi.org/10.24432/C5BW33. Licencia CC BY 4.0. Se publica un análisis derivado con filtros, agregaciones y visualizaciones. La fuente no avala las conclusiones.

Caso y materiales preparados con asistencia de Codex. El objetivo de aprendizaje es revisar, modificar y poder explicar cada decisión antes de presentar el proyecto en entrevistas.

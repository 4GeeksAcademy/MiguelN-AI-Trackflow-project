# Carpeta `scripts`

Esta carpeta contiene **scripts auxiliares** del monorepo: automatizaciones de desarrollo, utilidades de mantenimiento, tareas repetitivas (setup, lint, migraciones, generación de datos, etc.) y tooling interno.

- **Propósito principal**: agrupar herramientas de soporte que no pertenecen a una app/agente/pipeline específico, pero facilitan el trabajo del equipo.
- **Recomendación**: documenta cada script (qué hace, parámetros, requisitos, ejemplos de uso) y procura que sean reproducibles (y seguros) en distintos entornos.

## Tareas programadas

- `nightly_export.py`: la tarea nocturna de telemetría (ticket DEV-53). Exporta el día UTC anterior de `telemetry_events` a `data/raw/telemetry_<fecha>.csv` (solo si ese archivo no existe), ejecuta el pipeline de rendimiento del negocio para la última semana cerrada y registra la ejecución en `job_runs` a través de `services/job_runner`. Una sola ejecución a la vez y una ejecución completada por día. `python scripts/nightly_export.py`, o `TARGET_DATE=AAAA-MM-DD` para otro día ya terminado.
- `cron/nightly_export.sh` y `cron/nightly_export.crontab`: la entrada del crontab del sistema (todos los días a las 03:30) y el wrapper que llama, que escribe en `logs/nightly_export.log`.

Diseño, decisión de planificación y operación: [`docs/NIGHTLY_EXPORT.md`](../docs/NIGHTLY_EXPORT.md).

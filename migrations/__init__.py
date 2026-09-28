"""Migrations versionadas e aditivas do banco PMS."""

from . import v0001_operational_columns, v0002_editing_and_price_history, v0003_apontamento_pwa, v0004_application_settings, v0005_propostas_medicao, v0006_propostas_pagamento, v0007_soft_delete_lancamentos, v0008_employee_service_prices, v0009_uppercase_names


MIGRATIONS = (
    v0001_operational_columns,
    v0002_editing_and_price_history,
    v0003_apontamento_pwa,
    v0004_application_settings,
    v0005_propostas_medicao,
    v0006_propostas_pagamento,
    v0007_soft_delete_lancamentos,
    v0008_employee_service_prices,
    v0009_uppercase_names,
)

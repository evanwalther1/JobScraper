from app.connectors.base import Connector, JobPosting
from app.connectors.greenhouse import GreenhouseConnector
from app.connectors.oracle import OracleConnector
from app.connectors.paylocity import PaylocityConnector
from app.connectors.ukg import UKGConnector
from app.connectors.workday import WorkdayConnector
from app.models import Source

# Adding a new ATS = write a Connector subclass and register it here.
CONNECTORS: dict[Source, type[Connector]] = {
    Source.GREENHOUSE: GreenhouseConnector,
    Source.WORKDAY: WorkdayConnector,
    Source.UKG: UKGConnector,
    Source.PAYLOCITY: PaylocityConnector,
    Source.ORACLE: OracleConnector,
}

__all__ = ["CONNECTORS", "Connector", "JobPosting"]

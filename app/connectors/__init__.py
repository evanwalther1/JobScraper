from app.connectors.avature import AvatureConnector
from app.connectors.bamboohr import BambooHRConnector
from app.connectors.base import Connector, JobPosting
from app.connectors.breezy import BreezyConnector
from app.connectors.cats import CatsConnector
from app.connectors.eightfold import EightfoldConnector
from app.connectors.greenhouse import GreenhouseConnector
from app.connectors.oracle import OracleConnector
from app.connectors.paylocity import PaylocityConnector
from app.connectors.successfactors import SuccessFactorsConnector
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
    Source.CATS: CatsConnector,
    Source.EIGHTFOLD: EightfoldConnector,
    Source.SUCCESSFACTORS: SuccessFactorsConnector,
    Source.AVATURE: AvatureConnector,
    Source.BAMBOOHR: BambooHRConnector,
    Source.BREEZY: BreezyConnector,
}

__all__ = ["CONNECTORS", "Connector", "JobPosting"]

from enum import Enum
from pydantic import BaseModel


class IntentOutput(BaseModel):
    domain: str
    intent: str

class DomainType(str, Enum):
    TAX = "tax"
    GENERAL = "general"
    CONTRACT = "contract"

class LegalIntent(str, Enum):
    # Tax domain intents
    PREDICTING_LAWSUIT = "predicting_lawsuit"
    APPEAL_COURT_DECISION = "appeal_court_decision"
    APPEAL_TAX_ADMIN = "appeal_tax_admin"

    # General domain intents
    ADMIN_LITIGATION = "admin_litigation"
    JUDICIAL_REVIEW = "judicial_review"

    # Contract domain intents
    CONTRACT_TEMPLATE_GENERATION = "contract_template_generation"
    CONTRACT_RISK_ANALYSIS = "contract_risk_analysis"

    GENERAL_LEGAL = "general_legal"
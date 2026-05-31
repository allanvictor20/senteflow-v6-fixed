"""
SenteFlow — BusinessProfile (IDEA 08 — Business Profile Memory)
================================================================
Per-org business context injected into every Gemini system prompt.
"""

from typing import Optional
from pydantic import BaseModel, Field


class BusinessProduct(BaseModel):
    name: str
    price: float
    unit: str = "unit"
    currency: str = "UGX"
    is_default: bool = False  # True = "the usual"

    def display(self) -> str:
        return f"{self.name} @ {self.currency} {self.price:,.0f}/{self.unit}"


class BusinessProfile(BaseModel):
    org_id: str
    name: str = ""
    business_type: str = ""
    location: str = ""
    products: list[BusinessProduct] = Field(default_factory=list)
    credit_policy: str = ""
    operating_hours: str = ""
    owner_name: str = ""
    owner_phone: str = ""
    currency: str = "UGX"
    notes: str = ""

    def to_system_prompt_section(self) -> str:
        lines: list[str] = []
        if self.name:
            lines.append(f"Business name: {self.name}")
        if self.business_type:
            lines.append(f"Business type: {self.business_type}")
        if self.location:
            lines.append(f"Location: {self.location}")
        if self.owner_name:
            lines.append(f"Owner: {self.owner_name}")
        if self.operating_hours:
            lines.append(f"Hours: {self.operating_hours}")
        if self.credit_policy:
            lines.append(f"Credit policy: {self.credit_policy}")
        if self.currency:
            lines.append(f"Default currency: {self.currency}")
        if self.products:
            default = [p for p in self.products if p.is_default]
            others = [p for p in self.products if not p.is_default]
            if default:
                lines.append("Default item ('the usual'): " + ", ".join(p.display() for p in default))
            if others:
                lines.append("Other products: " + ", ".join(p.display() for p in others[:8]))
        if self.notes:
            lines.append(f"Owner notes: {self.notes}")
        if not lines:
            return ""
        return "\n--- Business Profile ---\n" + "\n".join(lines) + "\n--- End Business Profile ---\n"

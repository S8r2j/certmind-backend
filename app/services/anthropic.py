import anthropic
from app.core.config import settings

anthropic_client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

EXAM_METADATA = {
    "aws-cloud-practitioner": {
        "title": "AWS Cloud Practitioner",
        "code": "CLF-C02",
        "domains": [
            {"name": "Cloud Concepts", "weight": 0.24},
            {"name": "Security and Compliance", "weight": 0.30},
            {"name": "Cloud Technology and Services", "weight": 0.34},
            {"name": "Billing, Pricing, and Support", "weight": 0.12},
        ],
    },
    "aws-ai-practitioner": {
        "title": "AWS AI Practitioner",
        "code": "AIF-C01",
        "domains": [
            {"name": "Fundamentals of AI and ML", "weight": 0.20},
            {"name": "Fundamentals of Generative AI", "weight": 0.24},
            {"name": "Applications of Foundation Models", "weight": 0.28},
            {"name": "Guidelines for Responsible AI", "weight": 0.14},
            {"name": "Security, Compliance, and Governance for AI Solutions", "weight": 0.14},
        ],
    },
    "aws-solutions-architect": {
        "title": "AWS Solutions Architect Associate",
        "code": "SAA-C03",
        "domains": [
            {"name": "Design Secure Architectures", "weight": 0.30},
            {"name": "Design Resilient Architectures", "weight": 0.26},
            {"name": "Design High-Performing Architectures", "weight": 0.24},
            {"name": "Design Cost-Optimized Architectures", "weight": 0.20},
        ],
    },
}

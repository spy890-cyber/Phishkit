TEMPLATES = {
    "teams": {
        "name":"Microsoft Teams Meeting", "logo":"/static/logos/teams.svg",
        "accent":"#5b5fc7", "noun":"meeting", "header":"Microsoft Teams",
    },
    "teams_doc": {
        "name":"Microsoft Teams Document", "logo":"/static/logos/teams.svg",
        "accent":"#5b5fc7", "noun":"document", "header":"Microsoft Teams",
    },
    "adobe": {
        "name":"Adobe Acrobat Sign", "logo":"/static/logos/adobe.svg",
        "accent":"#ec1c24", "noun":"document", "header":"Adobe Acrobat Sign",
    },
    "docusign": {
        "name":"DocuSign", "logo":"/static/logos/docusign.svg",
        "accent":"#4c00ff", "noun":"document", "header":"docusign",
    },
    "generic": {
        "name":"Verify your identity", "logo":"/static/logos/ms.svg",
        "accent":"#0f6cbd", "noun":"document", "header":"Microsoft",
    },
}

def get(tid):
    return TEMPLATES.get(tid, TEMPLATES["generic"])
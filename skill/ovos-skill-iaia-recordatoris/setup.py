#!/usr/bin/env python3
from setuptools import setup

PLUGIN_ENTRY_POINT = "ovos-skill-iaia-recordatoris.iaia=ovos_skill_iaia_recordatoris:IaiaRecordatorisSkill"

setup(
    name="ovos-skill-iaia-recordatoris",
    version="0.1.0",
    description="Iaia: recordatoris per data i hora dits per veu en català (sense avisos anticipats)",
    packages=["ovos_skill_iaia_recordatoris"],
    package_data={"ovos_skill_iaia_recordatoris": ["locale/*/*/*", "res/*"]},
    install_requires=[],           # a propósito vacío: no debe tocar ovos-workshop ni ovos-bus-client
    entry_points={"ovos.plugin.skill": PLUGIN_ENTRY_POINT},
)

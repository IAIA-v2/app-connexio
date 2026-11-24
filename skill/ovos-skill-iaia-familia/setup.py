#!/usr/bin/env python3
from setuptools import setup

PLUGIN_ENTRY_POINT = "ovos-skill-iaia-familia.iaia=ovos_skill_iaia_familia:IaiaFamiliaSkill"

setup(
    name="ovos-skill-iaia-familia",
    version="0.1.0",
    description="Iaia: dice un mensaje de la familia, escucha la respuesta y se la devuelve (puente iaia-bridge)",
    packages=["ovos_skill_iaia_familia"],
    package_data={"ovos_skill_iaia_familia": ["locale/*/*/*"]},
    install_requires=[],           # a propósito vacío: no debe tocar ovos-workshop ni ovos-bus-client
    entry_points={"ovos.plugin.skill": PLUGIN_ENTRY_POINT},
)

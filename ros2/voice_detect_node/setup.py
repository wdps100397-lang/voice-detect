from setuptools import setup

setup(
    name="voice_detect_node",
    version="0.1.0",
    packages=["voice_detect_node"],
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/voice_detect_node"]),
        ("share/voice_detect_node", ["package.xml"]),
    ],
    install_requires=["setuptools"],
    zip_safe=True,
    maintainer="voice_detect maintainers",
    maintainer_email="maintainer@example.com",
    description="Real USB speaker identity producer",
    license="Apache-2.0",
    entry_points={"console_scripts": ["speaker_identity = voice_detect_node.node:main"]},
)

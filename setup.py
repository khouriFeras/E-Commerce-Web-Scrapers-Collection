#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Setup script for E-Commerce Scrapers Collection
"""

from setuptools import setup, find_packages
import os

# Read the README file
def read_readme():
    with open("README.md", "r", encoding="utf-8") as fh:
        return fh.read()

# Read requirements
def read_requirements():
    with open("requirements.txt", "r", encoding="utf-8") as fh:
        return [line.strip() for line in fh if line.strip() and not line.startswith("#")]

setup(
    name="ecommerce-scrapers",
    version="1.0.0",
    author="E-Commerce Scrapers Team",
    author_email="your-email@example.com",
    description="A comprehensive collection of Python web scrapers for various e-commerce websites",
    long_description=read_readme(),
    long_description_content_type="text/markdown",
    url="[PRIVATE_REPO_URL]",
    project_urls={
        "Bug Reports": "[PRIVATE_REPO_URL]/issues",
        "Source": "[PRIVATE_REPO_URL]",
        "Documentation": "[PRIVATE_REPO_URL]#readme",
    },
    packages=find_packages(),
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Developers",
        "Intended Audience :: Science/Research",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Programming Language :: Python :: 3.13",
        "Topic :: Internet :: WWW/HTTP :: Browsers",
        "Topic :: Software Development :: Libraries :: Python Modules",
        "Topic :: Text Processing :: Markup :: HTML",
        "Topic :: Scientific/Engineering :: Information Analysis",
    ],
    python_requires=">=3.8",
    install_requires=read_requirements(),
    extras_require={
        "dev": [
            "pytest>=7.0.0",
            "pytest-cov>=4.0.0",
            "black>=22.0.0",
            "flake8>=5.0.0",
            "mypy>=1.0.0",
            "pre-commit>=2.20.0",
        ],
        "docs": [
            "sphinx>=5.0.0",
            "sphinx-rtd-theme>=1.0.0",
            "myst-parser>=0.18.0",
        ],
    },
    entry_points={
        "console_scripts": [
            "arabi-mart=arabiMart:main",
            "birdsland-scraper=birdsland_scraper:main",
            "ferplast-scraper=ferplast_scraper:main",
            "jo-cell-scraper=jo_cell_scraper:main",
            "layor-group=layorGroup:main",
            "scraper-goat=scraperGoat:main",
            "woolapet-scraper=woolapet_scraper:main",
            "zepter-scraper=zepter_scraper:main",
            "samsung-aci=samsungaci:main",
        ],
    },
    keywords=[
        "web-scraping",
        "e-commerce",
        "selenium",
        "playwright",
        "data-extraction",
        "automation",
        "pandas",
        "beautifulsoup",
    ],
    include_package_data=True,
    zip_safe=False,
)

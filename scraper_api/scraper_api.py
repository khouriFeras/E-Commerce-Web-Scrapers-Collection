#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Scraper API Server
Provides REST API for n8n to call the universal scraper
"""

from flask import Flask, request, jsonify
import os
import time
import pandas as pd
from typing import List, Dict, Any
import tempfile
import json

# Import the universal scraper functions
from universal_selenium_scraper import build_driver, run_for_sku

app = Flask(__name__)

@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint."""
    return jsonify({"status": "healthy", "message": "Scraper API is running"})

@app.route('/scrape-single', methods=['POST'])
def scrape_single():
    """Scrape a single SKU."""
    try:
        data = request.json
        site_url = data.get('site_url')
        sku = data.get('sku')
        pause = data.get('pause', 1.0)
        headful = data.get('headful', False)
        
        if not site_url or not sku:
            return jsonify({"error": "site_url and sku are required"}), 400
        
        print(f"🔍 Scraping SKU: {sku} from {site_url}")
        
        # Run the universal scraper
        driver = build_driver(headful)
        try:
            body_html, image_src, product_url = run_for_sku(driver, site_url, sku, pause)
            
            result = {
                "sku": sku,
                "site_url": site_url,
                "product_url": product_url,
                "description": body_html,
                "images": image_src,
                "image_count": len(image_src.split(';')) if image_src else 0,
                "status": "SUCCESS" if (body_html and image_src) else "PARTIAL" if (body_html or image_src) else "FAILED",
                "timestamp": time.strftime('%Y-%m-%d %H:%M:%S')
            }
            
            return jsonify(result)
            
        finally:
            driver.quit()
            
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/scrape-batch', methods=['POST'])
def scrape_batch():
    """Scrape multiple SKUs from Excel data."""
    try:
        data = request.json
        site_url = data.get('site_url')
        sku_data = data.get('sku_data', [])  # List of SKUs
        pause = data.get('pause', 1.0)
        headful = data.get('headful', False)
        
        if not site_url or not sku_data:
            return jsonify({"error": "site_url and sku_data are required"}), 400
        
        print(f"🔍 Scraping {len(sku_data)} SKUs from {site_url}")
        
        results = []
        driver = build_driver(headful)
        
        try:
            for i, sku_info in enumerate(sku_data, 1):
                sku = sku_info.get('sku', '') if isinstance(sku_info, dict) else str(sku_info)
                print(f"📦 [{i}/{len(sku_data)}] Processing SKU: {sku}")
                
                try:
                    body_html, image_src, product_url = run_for_sku(driver, site_url, sku, pause)
                    
                    result = {
                        "sku": sku,
                        "site_url": site_url,
                        "product_url": product_url,
                        "description": body_html,
                        "images": image_src,
                        "image_count": len(image_src.split(';')) if image_src else 0,
                        "status": "SUCCESS" if (body_html and image_src) else "PARTIAL" if (body_html or image_src) else "FAILED",
                        "timestamp": time.strftime('%Y-%m-%d %H:%M:%S')
                    }
                    
                    results.append(result)
                    
                except Exception as e:
                    print(f"❌ Error processing {sku}: {e}")
                    results.append({
                        "sku": sku,
                        "site_url": site_url,
                        "product_url": "",
                        "description": "",
                        "images": "",
                        "image_count": 0,
                        "status": "ERROR",
                        "timestamp": time.strftime('%Y-%m-%d %H:%M:%S')
                    })
                
                # Small delay between requests
                if i < len(sku_data):
                    time.sleep(0.5)
            
            return jsonify({
                "results": results,
                "summary": {
                    "total": len(results),
                    "success": len([r for r in results if r['status'] == 'SUCCESS']),
                    "partial": len([r for r in results if r['status'] == 'PARTIAL']),
                    "failed": len([r for r in results if r['status'] in ['FAILED', 'ERROR']])
                }
            })
            
        finally:
            driver.quit()
            
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/scrape-excel', methods=['POST'])
def scrape_excel():
    """Scrape SKUs from uploaded Excel file."""
    try:
        if 'file' not in request.files:
            return jsonify({"error": "No file uploaded"}), 400
        
        file = request.files['file']
        if file.filename == '':
            return jsonify({"error": "No file selected"}), 400
        
        # Get parameters
        site_url = request.form.get('site_url')
        sku_column = request.form.get('sku_column', 'SKU')
        pause = float(request.form.get('pause', 1.0))
        headful = request.form.get('headful', 'false').lower() == 'true'
        
        if not site_url:
            return jsonify({"error": "site_url is required"}), 400
        
        # Save uploaded file temporarily
        with tempfile.NamedTemporaryFile(delete=False, suffix='.xlsx') as tmp_file:
            file.save(tmp_file.name)
            
            try:
                # Read Excel file
                df = pd.read_excel(tmp_file.name)
                print(f"📁 Loaded Excel file: {file.filename}")
                print(f"📋 Columns: {list(df.columns)}")
                print(f"📋 Total rows: {len(df)}")
                
                if sku_column not in df.columns:
                    return jsonify({"error": f"Column '{sku_column}' not found. Available columns: {list(df.columns)}"}), 400
                
                # Filter valid SKUs
                valid_skus = df[df[sku_column].notna() & (df[sku_column].astype(str).str.strip() != '')]
                print(f"✅ Found {len(valid_skus)} valid SKUs")
                
                if len(valid_skus) == 0:
                    return jsonify({"error": "No valid SKUs found"}), 400
                
                # Convert to list of SKUs
                sku_data = [{"sku": str(row[sku_column]).strip()} for _, row in valid_skus.iterrows()]
                
                # Call batch scraping
                batch_data = {
                    "site_url": site_url,
                    "sku_data": sku_data,
                    "pause": pause,
                    "headful": headful
                }
                
                # Simulate the batch scraping logic here
                results = []
                driver = build_driver(headful)
                
                try:
                    for i, sku_info in enumerate(sku_data, 1):
                        sku = sku_info['sku']
                        print(f"📦 [{i}/{len(sku_data)}] Processing SKU: {sku}")
                        
                        try:
                            body_html, image_src, product_url = run_for_sku(driver, site_url, sku, pause)
                            
                            result = {
                                "sku": sku,
                                "site_url": site_url,
                                "product_url": product_url,
                                "description": body_html,
                                "images": image_src,
                                "image_count": len(image_src.split(';')) if image_src else 0,
                                "status": "SUCCESS" if (body_html and image_src) else "PARTIAL" if (body_html or image_src) else "FAILED",
                                "timestamp": time.strftime('%Y-%m-%d %H:%M:%S')
                            }
                            
                            results.append(result)
                            
                        except Exception as e:
                            print(f"❌ Error processing {sku}: {e}")
                            results.append({
                                "sku": sku,
                                "site_url": site_url,
                                "product_url": "",
                                "description": "",
                                "images": "",
                                "image_count": 0,
                                "status": "ERROR",
                                "timestamp": time.strftime('%Y-%m-%d %H:%M:%S')
                            })
                        
                        # Small delay between requests
                        if i < len(sku_data):
                            time.sleep(0.5)
                    
                    return jsonify({
                        "results": results,
                        "summary": {
                            "total": len(results),
                            "success": len([r for r in results if r['status'] == 'SUCCESS']),
                            "partial": len([r for r in results if r['status'] == 'PARTIAL']),
                            "failed": len([r for r in results if r['status'] in ['FAILED', 'ERROR']])
                        }
                    })
                    
                finally:
                    driver.quit()
                    
            finally:
                # Clean up temporary file
                os.unlink(tmp_file.name)
                
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    print("🚀 Starting Scraper API Server...")
    print("📡 Available endpoints:")
    print("   GET  /health - Health check")
    print("   POST /scrape-single - Scrape single SKU")
    print("   POST /scrape-batch - Scrape multiple SKUs")
    print("   POST /scrape-excel - Scrape from Excel file")
    print()
    
    app.run(host='0.0.0.0', port=5000, debug=True)





.PHONY: setup pipeline dashboard

setup:
	pip install -r requirements.txt

pipeline:
	python load_data.py
	python analysis.py
	python stats_analysis.py
	python subset_analysis.py

dashboard:
	streamlit run dashboard.py --server.port 8501 --server.address 0.0.0.0

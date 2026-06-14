
import os
from sqlmodel import Session, create_engine, select
from services.chartedge_core.database import DynamicParameter

DATABASE_URL = os.getenv("DATABASE_URL")
engine = create_engine(DATABASE_URL)

print("Starting test query...")
with Session(engine) as session:
    try:
        statement = select(DynamicParameter)
        results = session.exec(statement)
        data = list(results.all())
        print(f"Success! Fetched {len(data)} parameters")
    except Exception as e:
        print(f"Error: {e}")

import uuid
from sqlmodel import Session
from db.session import engine
from services.search import search_bugs

def debug_search():
    with Session(engine) as session:
        # Get an existing company ID
        from db.models import Company
        from sqlmodel import select
        company = session.exec(select(Company).limit(1)).first()
        if not company:
            print("No companies found")
            return
            
        try:
            print("Running search...")
            bugs = search_bugs(session, company.id, "Test", {})
            print(f"Search successful! Found {len(bugs)} bugs.")
        except Exception as e:
            print("Search failed with exception:")
            import traceback
            traceback.print_exc()

if __name__ == "__main__":
    debug_search()

#!/usr/bin/env python3
"""
Jira Dominator Command Line Interface
A working alternative to the menu bar app
"""

import sys
import os
from pathlib import Path

# Add current directory to path
current_dir = Path(__file__).parent
sys.path.insert(0, str(current_dir))

def show_menu():
    """Show the main menu."""
    print("\n" + "="*50)
    print("🔧 JIRA DOMINATOR - Command Line Interface")
    print("="*50)
    print("1. Test Jira Connection")
    print("2. Show Priority Issues (IS)")
    print("3. Show Priority Issues (VRD)")
    print("4. Show Priority Issues (Both)")
    print("5. Generate Report")
    print("6. Quick Search")
    print("7. Exit")
    print("="*50)

def test_connection():
    """Test Jira connection."""
    print("\n🧪 Testing Jira connection...")
    try:
        from jira_dominator import JiraDominator
        dominator = JiraDominator()
        
        # Test search
        result = dominator.search_issues_direct('project = "IS" ORDER BY updated DESC', max_results=1)
        
        if 'issues' in result and len(result['issues']) > 0:
            print("✅ Jira connection successful!")
            print(f"Found {len(result['issues'])} issues in IS project")
        else:
            print("⚠️ Connected but no issues found")
            
    except Exception as e:
        print(f"❌ Jira connection failed: {e}")

def show_priority_issues(project=None):
    """Show priority issues for specified project or both."""
    print(f"\n📋 Fetching priority issues...")
    
    try:
        from jira_dominator import JiraDominator
        dominator = JiraDominator()
        
        projects = []
        if project == "IS" or project is None:
            projects.append("IS")
        if project == "VRD" or project is None:
            projects.append("VRD")
        
        for proj in projects:
            print(f"\n🔍 {proj} Project - High Priority Issues:")
            print("-" * 40)
            
            result = dominator.search_issues_direct(
                f'project = "{proj}" AND priority in ("Highest", "High") ORDER BY updated DESC', 
                max_results=10
            )
            
            issues = result.get('issues', [])
            if issues:
                for issue in issues:
                    fields = issue['fields']
                    assignee = fields['assignee']['displayName'] if fields['assignee'] else 'Unassigned'
                    priority = fields['priority']['name']
                    status = fields['status']['name']
                    
                    print(f"🔴 {issue['key']}: {fields['summary']}")
                    print(f"   Priority: {priority} | Status: {status} | Assignee: {assignee}")
                    print(f"   Updated: {fields['updated']}")
                    print()
            else:
                print(f"   No high priority issues found in {proj}")
                
    except Exception as e:
        print(f"❌ Failed to fetch issues: {e}")

def generate_report():
    """Generate a comprehensive report."""
    print("\n📊 Generating comprehensive report...")
    
    try:
        from jira_dominator import JiraDominator
        dominator = JiraDominator()
        
        # Get issues from both projects
        is_result = dominator.search_issues_direct('project = "IS" ORDER BY updated DESC', max_results=20)
        vrd_result = dominator.search_issues_direct('project = "VRD" ORDER BY updated DESC', max_results=20)
        
        is_issues = is_result.get('issues', [])
        vrd_issues = vrd_result.get('issues', [])
        
        print(f"\n📈 REPORT SUMMARY")
        print("=" * 50)
        print(f"IS Project: {len(is_issues)} total issues")
        print(f"VRD Project: {len(vrd_issues)} total issues")
        print(f"Total Issues: {len(is_issues) + len(vrd_issues)}")
        
        # Count by priority
        is_high_priority = len([i for i in is_issues if i['fields']['priority']['name'] in ['Highest', 'High']])
        vrd_high_priority = len([i for i in vrd_issues if i['fields']['priority']['name'] in ['Highest', 'High']])
        
        print(f"\n🚨 HIGH PRIORITY ISSUES:")
        print(f"IS: {is_high_priority} | VRD: {vrd_high_priority} | Total: {is_high_priority + vrd_high_priority}")
        
        # Count by status
        is_statuses = {}
        vrd_statuses = {}
        
        for issue in is_issues:
            status = issue['fields']['status']['name']
            is_statuses[status] = is_statuses.get(status, 0) + 1
            
        for issue in vrd_issues:
            status = issue['fields']['status']['name']
            vrd_statuses[status] = vrd_statuses.get(status, 0) + 1
        
        print(f"\n📊 STATUS BREAKDOWN:")
        print("IS Project:")
        for status, count in is_statuses.items():
            print(f"  {status}: {count}")
        print("VRD Project:")
        for status, count in vrd_statuses.items():
            print(f"  {status}: {count}")
            
    except Exception as e:
        print(f"❌ Failed to generate report: {e}")

def quick_search():
    """Quick search functionality."""
    print("\n🔍 Quick Search")
    print("Enter your search query (e.g., 'bug', 'high priority', 'IS-1234'):")
    query = input("Search: ").strip()
    
    if not query:
        print("❌ No search query provided")
        return
    
    try:
        from jira_dominator import JiraDominator
        dominator = JiraDominator()
        
        # Build JQL query
        if query.upper().startswith(('IS-', 'VRD-')):
            # Specific issue key
            jql = f'key = "{query.upper()}"'
        elif 'bug' in query.lower():
            jql = f'issuetype = Bug AND (summary ~ "{query}" OR description ~ "{query}")'
        elif 'high' in query.lower() or 'priority' in query.lower():
            jql = f'priority in ("Highest", "High") AND (summary ~ "{query}" OR description ~ "{query}")'
        else:
            jql = f'summary ~ "{query}" OR description ~ "{query}"'
        
        print(f"\n🔍 Searching with query: {jql}")
        
        result = dominator.search_issues_direct(jql, max_results=10)
        issues = result.get('issues', [])
        
        if issues:
            print(f"\n📋 Found {len(issues)} issues:")
            print("-" * 40)
            
            for issue in issues:
                fields = issue['fields']
                assignee = fields['assignee']['displayName'] if fields['assignee'] else 'Unassigned'
                priority = fields['priority']['name']
                status = fields['status']['name']
                
                print(f"🔍 {issue['key']}: {fields['summary']}")
                print(f"   Priority: {priority} | Status: {status} | Assignee: {assignee}")
                print()
        else:
            print("❌ No issues found matching your search")
            
    except Exception as e:
        print(f"❌ Search failed: {e}")

def main():
    """Main CLI loop."""
    print("🚀 Jira Dominator CLI Starting...")
    
    # Check .env file
    env_file = current_dir / ".env"
    if not env_file.exists():
        print("⚠️ .env file not found!")
        print("Please create a .env file with your Jira credentials:")
        print("JIRA_URL=https://your-domain.atlassian.net")
        print("JIRA_EMAIL=your-email@domain.com")
        print("JIRA_API_TOKEN=your_api_token")
        return
    
    while True:
        show_menu()
        choice = input("\nEnter your choice (1-7): ").strip()
        
        if choice == "1":
            test_connection()
        elif choice == "2":
            show_priority_issues("IS")
        elif choice == "3":
            show_priority_issues("VRD")
        elif choice == "4":
            show_priority_issues()
        elif choice == "5":
            generate_report()
        elif choice == "6":
            quick_search()
        elif choice == "7":
            print("\n👋 Goodbye!")
            break
        else:
            print("❌ Invalid choice. Please enter 1-7.")
        
        input("\nPress Enter to continue...")

if __name__ == "__main__":
    main()

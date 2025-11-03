import os
import sys

sys.path.append(".")  # Ensure current directory is in path for imports

from app.agent.crews.crew_base import Agents

crew = Agents()

result = crew.final_answer().kickoff("What is the phone number of my.gov.uz?")

print(result.raw)
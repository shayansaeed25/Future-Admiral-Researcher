"""Minimal fake of the Google Sheets REST API to exercise sheet_layout end-to-end."""
import json, re
from urllib.parse import unquote
class Fake:
    def __init__(self, tabs=("Signals","Positions","System Health","Dashboard")):
        self.sheets={t:i+1 for i,t in enumerate(tabs)}; self.next=100; self.values={}; self.batches=[]; self.cf={}; self.charts={}
        self.calls=[]
    def request(self, method, path, **kw):
        self.calls.append((method,path))
        if method=="GET" and path.startswith("?fields"):
            return {"sheets":[{"properties":{"sheetId":i,"title":t},"conditionalFormats":[{}]*self.cf.get(t,0),"charts":[{"chartId":c} for c in self.charts.get(t,[])],"bandedRanges":[]} for t,i in self.sheets.items()]}
        if method=="GET" and path.startswith("values/"):
            key=unquote(path.split("values/")[1]).split("?")[0]
            tab=re.match(r"'(.+?)'!",key).group(1)
            cell=key.split("!")[1]
            return {"values":[[self.values.get((tab,cell),"")]]} if (tab,cell) in self.values else {}
        if method=="PUT" and path.startswith("values/"):
            key=unquote(path.split("values/")[1]).split("?")[0]; tab=re.match(r"'(.+?)'!",key).group(1); cell=key.split("!")[1]
            vals=kw["json"]["values"]; self.values[(tab,cell)]=vals[0][0] if len(vals)==1 and len(vals[0])==1 else vals
            if cell=="A1": self.values[(tab,"A1")]=vals
            return {}
        if method=="POST" and path==":batchUpdate":
            reqs=kw["json"]["requests"]; self.batches.append(reqs); replies=[]
            for r in reqs:
                json.dumps(r)
                if "addSheet" in r:
                    t=r["addSheet"]["properties"]["title"]; self.next+=1; self.sheets[t]=self.next
                    replies.append({"addSheet":{"properties":{"title":t,"sheetId":self.next}}})
                elif "deleteSheet" in r:
                    sid=r["deleteSheet"]["sheetId"]; self.sheets={t:i for t,i in self.sheets.items() if i!=sid}; replies.append({})
                elif "addConditionalFormatRule" in r:
                    sid=r["addConditionalFormatRule"]["rule"]["ranges"][0]["sheetId"]; t=[k for k,v in self.sheets.items() if v==sid][0]; self.cf[t]=self.cf.get(t,0)+1; replies.append({})
                elif "deleteConditionalFormatRule" in r: replies.append({})
                elif "addChart" in r:
                    t=[k for k,v in self.sheets.items() if v==r["addChart"]["chart"]["position"]["overlayPosition"]["anchorCell"]["sheetId"]][0]
                    self.charts.setdefault(t,[]).append(len(self.charts.get(t,[]))+1); replies.append({"addChart":{}})
                else: replies.append({})
            return {"replies":replies}
        return {}

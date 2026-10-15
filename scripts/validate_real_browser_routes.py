"""Explicit real-site validation; never executes on import or test discovery."""

def main():
    import json,sys,time
    from pathlib import Path
    sys.path.insert(0,str(Path('catalyst_workbench/gateway').resolve()))
    from browser_acquire import run_job
    root=Path('runtime_data/browser_live_20261015').resolve();root.mkdir(parents=True,exist_ok=True)
    cases=[
    ('elsevier',{'id':'elsevier-previous-control','doi':'10.1016/j.apsusc.2019.145153','title':'The effect of SO2 on NH3-SCO and SCR properties over Cu/SCR catalyst'},'https://doi.org/10.1016/j.apsusc.2019.145153'),
    ('nature',{'id':'nature-existing','doi':'10.1038/s41467-026-72879-7','title':'Insights into the mechanisms of NH3 inhibition on Cu-CHA SCR catalysts'},'https://www.nature.com/articles/s41467-026-72879-7'),
    ('mdpi',{'id':'mdpi-review','doi':'10.3390/catal14110755','title':'A Short Review of Layered Double Oxide-Based Catalysts for NH3-SCR: Synthesis and NOx Removal'},'https://www.mdpi.com/2073-4344/14/11/755')]
    rows=[]
    for label,record,url in cases:
     folder=root/label;folder.mkdir(exist_ok=True)
     job=folder/'job.json';job.write_text(json.dumps({'authorized':True,'record':record,'url':url},ensure_ascii=False,indent=2),encoding='utf-8')
     print('Testing real website:',label,flush=True);start=time.monotonic()
     try:
      run_job(job,profile=root/'test_profile',headless=False,timeout_seconds=35)
      row={'platform':label,'elapsed_s':round(time.monotonic()-start,1),'status':json.loads((folder/'status.json').read_text(encoding='utf-8'))}
      if (folder/'result.json').is_file():row['result']=json.loads((folder/'result.json').read_text(encoding='utf-8'))
      if (folder/'observations.json').is_file():row['observations']=json.loads((folder/'observations.json').read_text(encoding='utf-8'))
     except Exception as e:row={'platform':label,'error_type':type(e).__name__,'elapsed_s':round(time.monotonic()-start,1)}
     rows.append(row);(root/'validation.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')
     print(json.dumps(row,ensure_ascii=False),flush=True)


if __name__ == "__main__":
    main()

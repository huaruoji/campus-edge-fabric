import { useEffect, useState } from "react";
import type { ApiClient, JobTask } from "../api";
import { ErrorBox } from "./ui";

interface Detection {class_id: number; conf: number; xyxy: number[]}
const classes = "person,bicycle,car,motorcycle,airplane,bus,train,truck,boat,traffic light,fire hydrant,stop sign,parking meter,bench,bird,cat,dog,horse,sheep,cow,elephant,bear,zebra,giraffe,backpack,umbrella,handbag,tie,suitcase,frisbee,skis,snowboard,sports ball,kite,baseball bat,baseball glove,skateboard,surfboard,tennis racket,bottle,wine glass,cup,fork,knife,spoon,bowl,banana,apple,sandwich,orange,broccoli,carrot,hot dog,pizza,donut,cake,chair,couch,potted plant,bed,dining table,toilet,tv,laptop,mouse,remote,keyboard,cell phone,microwave,oven,toaster,sink,refrigerator,book,clock,vase,scissors,teddy bear,hair drier,toothbrush".split(",");
export function DetectionPreview({api, task}: {api: ApiClient; task: JobTask}) {
  const [result,setResult] = useState<{url:string; width:number; height:number; detections:Detection[]; provider:string} | null>(null);
  const [error,setError] = useState<unknown>(null);
  const input=task.inputs[0], output=task.outputs.find((o)=>o.name.endsWith(".json"));
  useEffect(()=>{
    if (!input || !output) return;
    const controller=new AbortController(); let url: string | null=null;
    void (async()=>{
      try {
        const [image,json] = await Promise.all([api.fetchBlob(input.url,controller.signal),api.fetchBlob(output.url,controller.signal)]);
        if(json.size>2000000) throw new Error("检测结果过大，请下载查看。");
        const parsed=JSON.parse(await json.text());
        if(!Array.isArray(parsed.size) || parsed.size.length!==2 || !parsed.size.every((v:unknown)=>typeof v==="number" && Number.isFinite(v) && v>0) || !Array.isArray(parsed.detections)) throw new Error("检测结果格式不正确。");
        const detections: Detection[]=parsed.detections.filter((d:Detection)=>Number.isInteger(d.class_id) && Number.isFinite(d.conf) && Array.isArray(d.xyxy) && d.xyxy.length===4 && d.xyxy.every(Number.isFinite)).slice(0,1000);
        if(controller.signal.aborted) return;
        url=URL.createObjectURL(image); setResult({url,width:parsed.size[0],height:parsed.size[1],detections,provider:String(parsed.provider??"")});
      } catch(cause) {if(!controller.signal.aborted) setError(cause);}
    })();
    return ()=>{controller.abort(); if(url) URL.revokeObjectURL(url);};
  },[api,input,output]);
  if(!input || !output) return null;
  if(error) return <ErrorBox error={error} compact />;
  if(!result) return <p className="field-hint">正在加载检测图…</p>;
  return <section className="detection-result" aria-label="目标检测可视化"><h4>检测到 {result.detections.length} 个目标</h4>
    <svg role="img" aria-label={input.name+" 的检测框"} viewBox={`0 0 ${result.width} ${result.height}`} style={{width:"100%",maxHeight:480,background:"var(--surface-subtle)"}}>
      <image href={result.url} width={result.width} height={result.height} />
      {result.detections.map((d,i)=><g key={i}><rect x={d.xyxy[0]} y={d.xyxy[1]} width={Math.max(0,d.xyxy[2]-d.xyxy[0])} height={Math.max(0,d.xyxy[3]-d.xyxy[1])} fill="none" stroke="var(--accent)" strokeWidth={Math.max(result.width/400,1)} /><text x={d.xyxy[0]} y={Math.max(result.height/40,d.xyxy[1]-4)} fontSize={Math.max(result.height/35,10)} fill="var(--accent)" stroke="white" strokeWidth={.4} paintOrder="stroke">{classes[d.class_id]??`类别 ${d.class_id}`} {(d.conf*100).toFixed(1)}%</text></g>)}
    </svg><p className="field-hint">执行引擎：{result.provider}。检测结果是模型输出，未标记为人工真值。</p>
  </section>;
}

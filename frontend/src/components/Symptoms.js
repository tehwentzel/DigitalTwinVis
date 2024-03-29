import React, {useState, useEffect, useRef, useMemo} from 'react';
import useSVGCanvas from './useSVGCanvas.js';
import Utils from '../modules/Utils.js';
import * as d3 from 'd3';
import * as constants from "../modules/Constants.js";
import SymptomVisD3 from './SymptomVisD3.js';



export default function Symptoms(props){

    const ref = useRef(null);
    const symptomsToPlot = ['drymouth','choke'];
    
    const [plots,setPlots] = useState(<></>);

    const cWidth = ref.current? ref.current.clientWidth: 0;
    useEffect(()=>{
        if(props.symptoms !== undefined){
            var sList = Object.keys(props.symptoms.treated.symptoms)
            const sSort = s => props.symptoms.treated.symptoms[s].means[props.symptoms.treated.symptoms[s].means.length-1] + props.symptoms.untreated.symptoms[s].means[props.symptoms.untreated.symptoms[s].means.length-1];
            sList.sort((a,b) => sSort(b) - sSort(a));
            const treated = props.symptoms.treated;
            const untreated = props.symptoms.untreated;
            console.log('redraw')
            const newPlots = sList.map((sName,i)=>{
                i = parseInt(i);
                return (<div key={'symptoms'+sName+i} style={{'height': '10em','width': '95%','margin': '.1em','marginTop':'1em'}}>
                    <SymptomVisD3
                        name={sName}
                        treated={treated.symptoms[sName]}
                        untreated={untreated.symptoms[sName]}
                        treatedIds={treated.ids}
                        untreatedIds={untreated.ids}
                        treatedDists={treated.dists}
                        untreatedDists={untreated.dists}
                        dates={props.symptoms.dates}
                        width={ref.current? ref.current.clientWidth: '100%'}
                    ></SymptomVisD3>
                    </div>
                    )
            });
            setPlots(newPlots)
        }
    },[props.symptoms,cWidth])

    return (
        <div
            style={{'height':'95%','width':'100%'}}
            className={'scroll'}
            ref={ref}
        >
            {plots}
        </div>
    );
}

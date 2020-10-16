
import { listNanopubs, postNewNanopub, describeNanopub, deleteNanopub, lodPrefix } from 'utilities/nanopub'
import { goToView } from 'utilities/views' 

// TODO: Check whether this is necessary
const defaultContext= { 
  "dcat": "http://w3.org/ns/dcat#",
  "dct": "http://purl.org/dc/terms/",
  "vcard": "http://www.w3.org/2006/vcard/ns#",
  "foaf": "http://xmlns.com/foaf/0.1/",
  
  "title": "dct:title",
  "description": "dct:description",
  
  "contactpoint": "dcat:contactpoint", 
  "cpemail": "vcard:email",
  "cpfirstname": "vcard:given-name",
  "cplastname": "vcard:family-name",
  "individual": "vcard:individual",
  
  "name": "foaf:name",
  "contributor": "dct:contributor",         
  "organization": "foaf:Organization",
  "author": "dct:creator", 
  "person": "foaf:Person",
  "lastname":"foaf:lastname",
  "firstname":"foaf:firstname",
  "onbehalfof":"http://www.w3.org/ns/prov#actedOnBehalfOf",

  "datepub": "dct:issued",
  "datemod": "dct:modified",
  
  "refby":"dct:isReferencedBy",
  "repimage":"foaf:depiction",
  "distribution": "dcat:distribution"
}

const defaultDataset = {
  title: "",
  description: "",
  contactpoint: {
    "@type": "individual",
    cpfirstname: "",
    cplastname: "",
    cpemail: "",
  },
  contributor: [],
  author: [],
  datepub: {
    "@type": "date",
    "@value": "",
  },
  datemod: {
    "@type": "date",
    "@value": "",
  },
  refby: [], 
  distribution:{
    accessURL: null,
  },
  depiction: {
    name: '',
    accessURL: null,
  }
}


const datasetType = 'http://www.w3.org/ns/dcat#Dataset' 

const dcat = "http://w3.org/ns/dcat#"
const dct = "http://purl.org/dc/terms/"
const vcard = "http://www.w3.org/2006/vcard/ns#"
const foaf = "http://xmlns.com/foaf/0.1/"

const datasetFieldUris = {
  baseSpec: 'http://semanticscience.org/resource/hasValue', 
  title: `${dct}title`,
  description: `${dct}description`, 

  contactpoint: `${dcat}contactpoint`,
  cpemail: `${vcard}email`,
  cpfirstname: `${vcard}given-name`,
  cplastname: `${vcard}family-name`,
  individual: `${vcard}individual`,

  author: `${dct}creator`,
  name: `${foaf}name`,
  contributor: `${dct}contributor`,         
  organization: `${foaf}Organization`, 
  person: `${foaf}Person`, 
  onbehalfof:"http://www.w3.org/ns/prov#actedOnBehalfOf",

  datepub: `${dct}issued`,
  datemod: `${dct}modified`,
  date: 'https://www.w3.org/2001/XMLSchema#date',

  refby: `${dct}isReferencedBy`,

  distribution: `${dcat}distribution`,
  depiction: `${foaf}depiction`,
  hasContent: 'http://vocab.rpi.edu/whyis/hasContent',
  accessURL: `${dcat}accessURL`,
} 

const datasetPrefix = 'dataset' 

// Generate a randum uuid, or use current if exists
function generateDatasetId (guuid) {
  var datasetId;
  if (arguments.length === 0) { 
    const { v4: uuidv4 } = require('uuid');
    datasetId = uuidv4();
  } else {
    datasetId = guuid;
  }
  // const datasetId = Date.now();  
  return `${lodPrefix}/${datasetPrefix}/${datasetId}`
} 

function buildDatasetLd (dataset) {
  dataset = Object.assign({}, dataset)
  dataset.context = JSON.stringify(dataset.context)
  const datasetLd =  {
    // '@context': defaultContext,
    '@id': dataset.uri,
    '@type': [datasetType],
    // [foafDepictionUri]: {
    //   '@id': `${dataset.uri}_depiction`,
    //   [hasContentUri]: dataset.depiction
    // }
  }

  Object.entries(dataset)
    .filter(([field, value]) => datasetFieldUris[field])
    .forEach(([field, value]) => {  
      var ldValues = {};
      if ((value!=="")&&(value!==null)){ 
        ldValues = recursiveFieldSetter([field, value]);
        datasetLd[datasetFieldUris[field]] = [ldValues];
      }
    })
  return datasetLd
}

// Helper for assigning values into JSON-LD format
function recursiveFieldSetter ([field, value]) { 
  var fieldDict = {} 
  for (var val in value) {  
    if (Array.isArray(value)){
      fieldDict[val] = recursiveFieldSetter([field, value[val]])
    } 
    else if ((val === '@type') || (val === '@value') || (val === '@id')){ 
      fieldDict[val] = value[val];
      if (datasetFieldUris.hasOwnProperty(value[val])){
        fieldDict[val] = datasetFieldUris[value[val]];
      }
    }
    else if (datasetFieldUris.hasOwnProperty(val)){ 
      fieldDict[datasetFieldUris[val]] = recursiveFieldSetter([datasetFieldUris[val], value[val]]);
    } else {
      fieldDict['@value'] = value;
    }
  }
  return fieldDict
}

// Blank dataset
function getDefaultDataset () { 
  return Object.assign({}, defaultDataset)
}

// Load for editing
function loadDatasetFromNanopub(nanopubUri, datasetUri) {
  return describeNanopub(nanopubUri)
    .then((describeData) => {
      const assertion_id = `${nanopubUri}_assertion`
      for (let graph of describeData) {
        if (graph['@id'] === assertion_id) {
          for (let resource of graph['@graph']) {
            if (resource['@id'] === datasetUri) {
              return extractDataset(resource)
            }
          }
        }
      }
    })
}

// Load for editing
function loadDataset (datasetUri) {
  return listNanopubs(datasetUri)
    .then(nanopubs => {
      if (nanopubs.length > 0) {
        const nanopubUri = nanopubs[0].np
        return loadDatasetFromNanopub(nanopubUri, datasetUri)
      }
    })
}

// Extract information from dataset in JSONLD format
// TODO: re-write to assign all values in correct format
function extractDataset (datasetLd) {
  const dataset = Object.assign({}, defaultDataset)

  Object.entries(defaultDataset)
    .forEach(([field]) => {
      // let value = "";
      let uri = datasetFieldUris[field];
      var val = datasetLd[uri];
      console.log(val)
      if ((uri in datasetLd) && (typeof val !== `undefined`) ){
        console.log(val[0])
        if (typeof val[0]['@value'] !== `undefined`){
          dataset[field] = datasetLd[uri][0]['@value']
        }  
      }
      // dataset[field] = value
    })
    
  return dataset
}


async function saveDataset (dataset, guuid) {
  let deletePromise = Promise.resolve()
  if (dataset.uri) {
    deletePromise = deleteDataset(dataset.uri)
  } else if (arguments.length === 1){
    dataset.uri = generateDatasetId()
  } else {
    dataset.uri = generateDatasetId(guuid)
  } 
  const datasetLd = buildDatasetLd(dataset) 
  await deletePromise
  try{
    return postNewNanopub(datasetLd)
  } catch(err){
    return alert(err)
  }
  
}

function deleteDataset (datasetUri) { 
  return listNanopubs(datasetUri)
    .then(nanopubs => {
      console.log("in delete")
      console.log(nanopub.np)
      Promise.all(nanopubs.map(nanopub => deleteNanopub(nanopub.np)))
    }
    )
}


// Reformat the auto-complete menu
let run;

const processAutocompleteMenu = () => {
    run = setInterval(() => {
        const floatList = document.getElementsByClassName("md-menu-content-bottom-start")
        if(floatList.length >= 1) {
            floatList[0].setAttribute("style", "z-index:1000 !important; width: 80%; max-width: 80%; position: absolute; top: 644px; left:50%; transform:translateX(-50%); will-change: top, left;")
            return status = true
        }
    }, 40)

    return run
}

export { getDefaultDataset, loadDataset, saveDataset, deleteDataset, processAutocompleteMenu}
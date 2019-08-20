from builtins import str
from builtins import range
from six import b
from six.moves.urllib.request import pathname2url
from six import iteritems

from rdflib.store import Store, VALID_STORE, NO_STORE
from rdflib.term import URIRef, BNode

from rdflib.graph import Graph

import requests
from uuid import uuid4
from urllib.parse import quote_plus

import rdflib
from elasticsearch import Elasticsearch

import json

import logging
logger = logging.getLogger(__name__)

__all__ = ['ElasticSearchStore']
import pkg_resources

elastic_index_settings = pkg_resources.resource_string(__name__, "elastic_index_settings.json")

#from rdflib.plugins.sparql import CUSTOM_EVALS
from rdflib.plugins.sparql.sparql import Query, AlreadyBound

from rdflib.plugins.sparql.parser import parseQuery, parseUpdate
from rdflib.plugins.sparql.algebra import translateQuery, translateUpdate

from rdflib.plugins.sparql.evalutils import (
    _filter, _eval, _join, _diff, _minus, _fillTemplate, _ebv, _val)

from rdflib.plugins.sparql.evaluate import evalPart
#from elastic_eval import evalPart
#from rdflib.plugins.sparql.evaluate import evalBGP as evalBGP_orig
#from rdflib.plugins.sparql.evaluate import evalGraph as evalGraph_orig

class ElasticSearchStore(Store):
    context_aware = True
    formula_aware = False
    transaction_aware = False
    graph_aware = True
    __prefix = {}
    __namespace = {}

    def __init__(self, configuration=None, identifier=None):
        self.__open = False
        self.__identifier = identifier
        super(ElasticSearchStore, self).__init__(configuration)

    def __get_identifier(self):
        return self.__identifier
    identifier = property(__get_identifier)

    def is_open(self):
        return self.__open

    def open(self, url, index, create=True):
        #self.url, _, self.index = url.rpartition('/')
        self.url, self.index = url, index
        self.es = Elasticsearch([self.url])

        self.__open = True

        return VALID_STORE

    def sync(self):
        if self.__open:
            pass

    def close(self, commit_pending_transaction=False):
        if self.__open:
            #self.db_env.close()
            self.__open = False

    def add(self, triple, context, quoted=False, txn=None, refresh=False):
        """\
        Add a triple to the store of triples.
        """
        (subject, predicate, object) = triple
        assert self.__open, "The Store must be open."
        assert context != self, "Can not add triple directly to store"
        self.addN([(subject,predicate,object,context)], refresh=refresh)

    def quads_to_jsonld(self, quads):
        """\
        Formats a list of RDF quads as JSON-LD.
        """
        graphs = {}
        resources = {}
        json_ld = []
        bnodes = {}


        def skolemize_bnodes(x):
            if isinstance(x, rdflib.Graph):
                x = x.identifier
            if isinstance(x, rdflib.BNode):
                if x not in bnodes:
                    bnodes[x] = '_:'+uuid4().hex
                return bnodes[x]
            else:
                return x
            
        for quad in quads:
            s, p, o, g = [skolemize_bnodes(x) for x in quad]
            if g not in graphs:
                graphs[g] = {"@id":str(g),"@graph":[]}
                json_ld.append(graphs[g])
            if (g,s) not in resources:
                resources[(g,s)] = {"@id":str(s), "@object" : []}
                graphs[g]['@graph'].append(resources[(g,s)])
            resource = resources[(g,s)]
            if str(p) not in resource:
                resource[str(p)] = []
            if isinstance(o, rdflib.Literal):
                obj = {"@value" : str(o)}
                if o.datatype is not None:
                    obj['@type'] = str(o.datatype)
                if o.language is not None:
                    obj['@lang'] = str(o.language)
            else:
                obj = {"@id" : str(o)}
            resource[str(p)].append(obj)
            resource['@object'].append(obj) # check if it's already there first?

        json_ld = json.dumps({ "graphs": json_ld })

        return json_ld
        
    def addN(self, quads, docid=None, refresh=False):
        """\
        Adds each item in the list of statements to a specific context. The
        quoted argument is interpreted by formula-aware stores to indicate this
        statement is quoted/hypothetical.
        """
        assert self.__open, "The Store must be open."

        json_ld = self.quads_to_jsonld(quads)

        if docid is None:
            docid = uuid4().hex
        else:
            docid = quote_plus(docid)

        #r = self.session.put(self.url+'/nanopublication/'+docid+'?refresh='+('wait_for' if refresh else 'false'),
        #                     headers={"Content-Type":"application/json"},
        #                     data=json_ld)
        #if r.status_code != 201:
        #    print(r.status_code)
        #    print(r.content)

        self.es.create(index=self.index, id=docid, body=json_ld, refresh='wait_for' if refresh else 'false')
    
                
    def remove(self, spo, context, txn=None):
        subject, predicate, object = spo
        assert False, "remove() is not implemented."

    def retire_nanopub(self, uri):
        """\
        Removes from the store a nanopublication that the given Resource object refers to.
        """

        query = {
          "query": {
            "nested": {
              "query": {
                "term": {
                  "graphs.@graph.@id": str(uri)
                }
              },
              "path": "graphs.@graph",
              "score_mode": "avg"
            }
          }
        }

        print("query is", query)
        self.es.indices.refresh(index=self.index)

        self.es.delete_by_query(index=self.index, body=query)

        #r = self.session.post(self.url+"/_delete_by_query", data=json.dumps(query))
        #print("Response:",r.status_code, r.content)

    def elastic_query(self, query):
        query = {
            "query": {
                "nested" : {
                    "path" : "graphs.@graph",
                    "score_mode" : "avg",
                    "query" : query
                }
            }
        }

        self.es.indices.refresh(index=self.index)

        #response = self.session.post(self.url+"/_search",data=json.dumps(query))

        return self.es.search(index=[self.index], body=json.dumps(query)) 


    def subgraph(self, query):
        """\
        Returns a graph containing all triples matching a given Elasticsearch query.
        """

        json_response = self.elastic_query(query)
        g = rdflib.ConjunctiveGraph()

        g.addN(json_ld_triples(json_response))
        return g

    def triples(self, spo=(None,None,None), context=None, txn=None):
        """Returns a generator over all the triples matching the specified pattern."""
        assert self.__open, "The Store must be open."

        subject, predicate, object = spo

        if context is not None and not isinstance(context, URIRef):
            if context == self or isinstance(context, BNode):
                context = None
            else:
                context = context.identifier
                
        if context == self or isinstance(context, BNode):
            context = None
        query = {
            "bool" : {
                "must" : [
                ]
            }
        }
        match_all = True
        if subject is not None:
            match_all = False
            query['bool']['must'].append( { "term" : { "graphs.@graph.@id" : str(subject) } } )
        if predicate is not None and object is None:
            match_all = False
            query['bool']['must'].append( { "exists" : { 'field': "graphs.@graph."+str(predicate) } } )
        if object is not None:
            match_all = False
            obj_path = ''
            if isinstance(object, rdflib.Literal):
                obj_path = obj_path + '.@value'
            else:
                obj_path = obj_path + '.@id'
            if predicate is not None:
                query['bool']['must'].append( { "term" : {
                      "graphs.@graph."+str(predicate) +obj_path : str(object)
                    } } )
            else:
                query['bool']['must'].append( { "term" : {
                     "graphs.@graph.@object" +obj_path : str(object)
                    } } )
        if context is not None:
            match_all = False
            query['bool']['must'].append( { "term" : {
                 "graphs.@id" : str(context)
                } } )

        if match_all:
            query = {'match_all': {}}

        json_response = self.elastic_query(query)

        if isinstance(context, Graph):
            context = context.identifier
        for s, p, o, g in json_ld_triples(json_response,
                                               subject, predicate, object, context):
            yield (s, p, o), Graph(store=self, identifier=g)

                            
    def __len__(self, context=None):
        assert self.__open, "The Store must be open."
        assert False, "Not implemented."
        return 0

    def bind(self, prefix, namespace):
        self.__prefix[namespace] = prefix
        self.__namespace[prefix] = namespace

    def namespace(self, prefix):
        return self.__namespace.get(prefix, None)

    def prefix(self, namespace):
        return self.__prefix.get(namespace, None)

    def namespaces(self):
       yield from iteritems(self.__namespace)

    def contexts(self, triple=None):
        assert False, "Not implemented."
        for i in range(0):
            yield(URIRef())

    def add_graph(self, graph, refresh=False):
        self.addN(((s,p,o,graph.identifier) for s,p,o in graph.triples((None, None, None))), refresh=refresh)

    def remove_graph(self, graph):
        if isinstance(graph, Graph):
            graph = graph.identifier
        # response = self.session.delete(self.url+"/nanopublication",graph.split('/')[-1])
        self.es.delete(index=self.index, id=graph.split('/')[-1]) #TODO where do I put "/nanopublication"? 


def json_ld_triples(json_response, subject=None, predicate=None, object=None, context=None):
    #print json_response
    if 'hits' not in json_response:
        return
    for hit in json_response['hits']['hits']:
        for graph in hit['_source']['graphs']:
            graphid = rdflib.URIRef(graph['@id'])
            if context is not None and context != graphid:
                continue
            for resource in graph['@graph']:
                resourceid = rdflib.URIRef(resource['@id'])
                if resourceid.startswith('_:'):
                    resourceid = rdflib.BNode(resourceid[2:])
                else:
              	    resourceid = rdflib.URIRef(resourceid)
                if subject is not None and subject != resourceid:
                    continue
                predicates = []
                if predicate is not None:
                    predicates = [str(predicate)]
                else:
                    predicates = list(resource.keys())
                for pred in predicates:
                    if pred == '@object' or pred == '@id':
                        continue
                    if pred not in resource:
                        continue
                    for obj in resource[pred]:
                        o = None
                        if '@id' in obj:
                            id = obj['@id']
                            if id.startswith('_:'):
                                o = rdflib.BNode(id[2:])
                            else:
                                o = rdflib.URIRef(id)
                        else:
                            kwargs = {}
                            if '@type' in obj:
                                kwargs['datatype'] = rdflib.URIRef(obj['@type'])
                            if '@lang' in obj:
                                kwargs['lang'] = obj['@lang']
                            o = rdflib.Literal(obj['@value'],**kwargs)
                        if object is not None and o != object:
                            continue
                        yield resourceid, rdflib.URIRef(pred), o, graphid

#CUSTOM_EVALS['BGP'] = evalBGP
#CUSTOM_EVALS['Graph'] = evalGraph

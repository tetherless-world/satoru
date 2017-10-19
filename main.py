# -*- coding:utf-8 -*-
import requests
try:
    import config
    print config.__package__
except:
    print "WARNING: missing config, using defaults file"
    import config_defaults as config

import os
import sys, collections
from empty import Empty
from flask import Flask, render_template, g, session, redirect, url_for, request, flash, abort, Response, stream_with_context, send_from_directory
import flask_ld as ld
import jinja2
from flask_ld.utils import lru
from flask_restful import Resource
from nanopub import NanopublicationManager, Nanopublication
import requests
from re import finditer

from flask_admin import Admin, BaseView, expose

import rdflib
from flask_security import Security, \
    UserMixin, RoleMixin, login_required
from flask_security.core import current_user
from flask_login import AnonymousUserMixin, login_user
from flask_security.forms import RegisterForm
from flask_security.utils import encrypt_password
from werkzeug.datastructures import ImmutableList
from flask_wtf import Form, RecaptchaField
from wtforms import TextField, TextAreaField, StringField, validators
import rdfalchemy
from rdfalchemy.orm import mapper
import sadi
import json
import sadi.mimeparse

import werkzeug.utils

from flask_mail import Mail, Message

from celery import Celery
from celery.schedules import crontab

import database

from datetime import datetime

import markdown

import rdflib.plugin
from rdflib.store import Store
from rdflib.parser import Parser
from rdflib.serializer import Serializer
from rdflib.query import ResultParser, ResultSerializer, Processor, Result, UpdateProcessor
from rdflib.exceptions import Error
rdflib.plugin.register('sparql', Result,
        'rdflib.plugins.sparql.processor', 'SPARQLResult')
rdflib.plugin.register('sparql', Processor,
        'rdflib.plugins.sparql.processor', 'SPARQLProcessor')
rdflib.plugin.register('sparql', UpdateProcessor,
        'rdflib.plugins.sparql.processor', 'SPARQLUpdateProcessor')

# apps is a special folder where you can place your blueprints
PROJECT_PATH = os.path.abspath(os.path.dirname(__file__))
sys.path.insert(0, os.path.join(PROJECT_PATH, "apps"))

basestring = getattr(__builtins__, 'basestring', str)

# we create some comparison keys:
# increase probability that the rule will be near or at the top
top_compare_key = False, -100, [(-2, 0)]
# increase probability that the rule will be near or at the bottom 
bottom_compare_key = True, 100, [(2, 0)]

class NamespaceContainer:
    @property
    def prefixes(self):
        result = {}
        for key, value in self.__dict__.items():
            if isinstance(value, Namespace):
                result[key] = value
        return result

NS = NamespaceContainer()
NS.RDFS = rdflib.RDFS
NS.RDF = rdflib.RDF
NS.rdfs = rdflib.Namespace(rdflib.RDFS)
NS.rdf = rdflib.Namespace(rdflib.RDF)
NS.owl = rdflib.OWL
NS.xsd   = rdflib.Namespace("http://www.w3.org/2001/XMLSchema#")
NS.dc    = rdflib.Namespace("http://purl.org/dc/terms/")
NS.dcelements    = rdflib.Namespace("http://purl.org/dc/elements/1.1/")
NS.auth  = rdflib.Namespace("http://vocab.rpi.edu/auth/")
NS.foaf  = rdflib.Namespace("http://xmlns.com/foaf/0.1/")
NS.prov  = rdflib.Namespace("http://www.w3.org/ns/prov#")
NS.skos = rdflib.Namespace("http://www.w3.org/2004/02/skos/core#")
NS.cmo = rdflib.Namespace("http://purl.org/twc/ontologies/cmo.owl#")
NS.sio = rdflib.Namespace("http://semanticscience.org/resource/")
NS.sioc_types = rdflib.Namespace("http://rdfs.org/sioc/types#")
NS.sioc = rdflib.Namespace("http://rdfs.org/sioc/ns#")
NS.np = rdflib.Namespace("http://www.nanopub.org/nschema#")
NS.graphene = rdflib.Namespace("http://vocab.rpi.edu/graphene/")
NS.ov = rdflib.Namespace("http://open.vocab.org/terms/")
NS.frbr = rdflib.Namespace("http://purl.org/vocab/frbr/core#")
    
from rdfalchemy import *
from flask_ld.datastore import *

# Setup Flask-Security
class ExtendedRegisterForm(RegisterForm):
    identifier = TextField('Identifier', [validators.Required()])
    givenName = TextField('Given Name', [validators.Required()])
    familyName = TextField('Family Name', [validators.Required()])

# Form for full-text search
class SearchForm(Form):
    search_query = StringField('search_query', [validators.DataRequired()])

def to_json(result):
    return json.dumps([dict([(key, value.value if isinstance(value, Literal) else value) for key, value in x.items()]) for x in result.bindings])

class App(Empty):

    def configure_extensions(self):
        Empty.configure_extensions(self)
        self.celery = Celery(self.name, broker=self.config['CELERY_BROKER_URL'], beat=True)
        self.celery.conf.update(self.config)
        
        app = self

        if 'root_path' in self.config:
            self.root_path = self.config['root_path']
        
        if 'SATORU_TEMPLATE_DIR' in self.config and app.config['SATORU_TEMPLATE_DIR'] is not None:
            my_loader = jinja2.ChoiceLoader(
                [jinja2.FileSystemLoader(p) for p in self.config['SATORU_TEMPLATE_DIR']] + 
                [app.jinja_loader]
            )
            app.jinja_loader = my_loader
        
        def setup_task(service):
            service.app = app
            print service
            result = None
            if service.query_predicate == self.NS.graphene.globalChangeQuery:
                result = process_resource
            else:
                result = process_nanopub
            result.service = lambda : service
            return result

        @self.celery.task
        def process_resource(uri, service_name):
            service = self.config['inferencers'][service_name]
            print service, uri
            resource = app.get_resource(uri)
            service.process_graph(resource.graph)

        @self.celery.task
        def process_nanopub(nanopub_uri, service_name):
            service = self.config['inferencers'][service_name]
            print service, nanopub_uri
            nanopub = app.nanopub_manager.get(nanopub_uri)
            service.process_graph(nanopub)

        def setup_periodic_task(task):
            @self.celery.task
            def find_instances():
                print "Triggered task", task['name']
                for x, in app.db.query(task['service'].get_query()):
                    task['do'](x)
            
            @self.celery.task
            def do_task(uri):
                print "Running task", task['name'], 'on', uri
                resource = app.get_resource(uri)
                result = task['service'].process_graph(resource.graph)

            task['service'].app = app
            task['find_instances'] = find_instances
            task['do'] = do_task

            return task
            
        app.inference_tasks = []
        if 'inference_tasks' in self.config:
            app.inference_tasks = [setup_periodic_task(task) for task in self.config['inference_tasks']]

        for task in app.inference_tasks:
            if 'schedule' in task:
                #print "Scheduling task", task['name'], task['schedule']
                self.celery.add_periodic_task(
                    crontab(**task['schedule']),
                    task['find_instances'].s(),
                    name=task['name']
                )
            else:
                task['find_instances'].delay()
        
        @self.celery.task()
        def update(nanopub_uri):
            '''gets called whenever there is a change in the knowledge graph.
            Performs a breadth-first knowledge expansion of the current change.'''
            #print "Updating on", nanopub_uri
            nanopub = app.nanopub_manager.get(nanopub_uri)
            nanopub_graph = ConjunctiveGraph(nanopub.store)
            if 'inferencers' in self.config:
                for name, service in self.config['inferencers'].items():
                    service.app = self
                    if service.query_predicate == self.NS.graphene.globalChangeQuery:
                        #print "checking", name, service.get_query()
                        for uri, in app.db.query(service.get_query()):
                            print "invoking", name, uri
                            process_resource(uri, name)
                    if service.query_predicate == self.NS.graphene.updateChangeQuery:
                        #print "checking", name, nanopub_uri, service.get_query()
                        if len(list(nanopub_graph.query(service.get_query()))) > 0:
                            print "invoking", name, nanopub_uri
                            process_nanopub(nanopub_uri, name)

        def run_update(nanopub_uri):
            update.delay(nanopub_uri)
        self.nanopub_update_listener = run_update

    def configure_database(self):
        """
        Database configuration should be set here
        """
        self.NS = NS
        self.NS.local = rdflib.Namespace(self.config['lod_prefix']+'/')

        self.admin_db = database.engine_from_config(self.config, "admin_")
        self.db = database.engine_from_config(self.config, "knowledge_")
        self.db.app = self
        load_namespaces(self.db,locals())
        Resource.db = self.admin_db

        self.vocab = Graph()
        #print URIRef(self.config['vocab_file'])
        self.vocab.load(open("default_vocab.ttl"), format="turtle")
        self.vocab.load(open(self.config['vocab_file']), format="turtle")

        self.role_api = ld.LocalResource(self.NS.prov.Role,"role", self.admin_db.store, self.vocab, self.config['lod_prefix'], RoleMixin)
        self.Role = self.role_api.alchemy

        self.user_api = ld.LocalResource(self.NS.prov.Agent,"user", self.admin_db.store, self.vocab, self.config['lod_prefix'], UserMixin)
        self.User = self.user_api.alchemy

        self.nanopub_api = ld.LocalResource(self.NS.np.Nanopublication,"pub", self.db.store, self.vocab, self.config['lod_prefix'], name="Graph")
        self.Nanopub = self.nanopub_api.alchemy

        self.classes = mapper(self.Role, self.User)
        self.datastore = RDFAlchemyUserDatastore(self.admin_db, self.classes, self.User, self.Role)
        self.security = Security(self, self.datastore,
                                 register_form=ExtendedRegisterForm)
        #self.mail = Mail(self)

    def weighted_route(self, *args, **kwargs):
        def decorator(view_func):
            compare_key = kwargs.pop('compare_key', None)
            # register view_func with route
            self.route(*args, **kwargs)(view_func)
    
            if compare_key is not None:
                rule = self.url_map._rules[-1]
                rule.match_compare_key = lambda: compare_key
    
            return view_func
        return decorator

    def map_entity(self, name):
        for importer in self.config['namespaces']:
            if importer.matches(name):
                new_name = importer.map(name)
                #print 'Found mapped URI', new_name
                return new_name, importer
        return None, None

    def find_importer(self, name):
        for importer in self.config['namespaces']:
            if importer.resource_matches(name):
                return importer
        return None


    class Entity (rdflib.resource.Resource):
        _this = None
        
        def this(self):
            if self._this is None:
                self._this = self._graph.app.get_entity(self.identifier)
            return self._this

        _description = None

        def description(self):
            if self._description is None:
#                try:
                result = Graph()
                try:
                    result += self._graph.query('''
construct {
    ?e ?p ?o.
    ?o rdfs:label ?label.
    ?o skos:prefLabel ?prefLabel.
    ?o dc:title ?title.
    ?o foaf:name ?name.
    ?o ?pattr ?oatter.
    ?oattr rdfs:label ?oattrlabel
} where {
    graph ?g {
      ?e ?p ?o.
    }
    ?g a np:Assertion.
    optional {
      ?e sio:hasAttribute|sio:hasPart ?o.
      ?o ?pattr ?oattr.
      optional {
        ?oattr rdfs:label ?oattrlabel.
      }
    }
    optional {
      ?o rdfs:label ?label.
    }
    optional {
      ?o skos:prefLabel ?prefLabel.
    }
    optional {
      ?o dc:title ?title.
    }
    optional {
      ?o foaf:name ?name.
    }
}''', initNs=NS.prefixes, initBindings={'e':self.identifier})
                except:
                    pass
                self._description = result.resource(self.identifier)
#                except Exception as e:
#                    print str(e), self.identifier
#                    raise e
            return self._description
        
    def get_resource(self, entity):
        mapped_name, importer = self.map_entity(entity)
    
        if mapped_name is not None:
            entity = mapped_name

        if importer is None:
            importer = self.find_importer(entity)

        if importer is not None:
            importer.load(entity, self.db, self.nanopub_manager)
            
        return self.Entity(self.db, entity)

    def configure_template_filters(self):
        import urllib
        from markupsafe import Markup
    
        @self.template_filter('urlencode')
        def urlencode_filter(s):
            if type(s) == 'Markup':
                s = s.unescape()
            s = s.encode('utf8')
            s = urllib.quote_plus(s)
            return Markup(s)
    
    def configure_views(self):

        def sort_by(resources, property):
            return sorted(resources, key=lambda x: x.value(property))

        class InvitedAnonymousUser(AnonymousUserMixin):
            '''A user that has been referred via kikm references but does not have a user account.'''
            def __init__(self):
                self.roles = ImmutableList()

            def has_role(self, *args):
                """Returns `False`"""
                return False

            def is_active(self):
                return True

            @property
            def is_authenticated(self):
                return True

        def camel_case_split(identifier):
            matches = finditer('.+?(?:(?<=[a-z])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])|$)', identifier)
            return [m.group(0) for m in matches]

        label_properties = [self.NS.skos.prefLabel, self.NS.RDFS.label, self.NS.dc.title, self.NS.foaf.name]

        @lru
        def get_remote_label(uri):
            for db in [self.db, self.admin_db]:
                g = Graph()
                try:
                    g += db.query('''construct { ?s ?p ?o} where { ?s ?p ?o.}''',
                                  initNs=self.NS.prefixes, initBindings=dict(s=uri))
                except:
                    pass
                resource_entity = g.resource(uri)
                if len(resource_entity.graph) == 0:
                    #print "skipping", db
                    continue
                for property in label_properties:
                    label = resource_entity.value(property)
                    if label is not None:
                        return label
                    
                if label is None or len(label) < 2:
                    name = [x.value for x in [resource_entity.value(self.NS.foaf.givenName),
                                              resource_entity.value(self.NS.foaf.familyName)] if x is not None]
                    if len(name) > 0:
                        label = ' '.join(name)
                        return label
            try:
                label = self.db.qname(uri).split(":")[1].replace("_"," ")
                return ' '.join(camel_case_split(label)).title()
            except Exception as e:
                print str(e), uri
                return str(uri)
        
        def get_label(resource):
            for property in label_properties:
                label = resource.value(property)
                #print "mem", property, label
                if label is not None:
                    return label
            return get_remote_label(resource.identifier)
            
        @self.before_request
        def load_forms():
            if 'API_KEY' in self.config:
                if 'API_KEY' in request.args and request.args['API_KEY'] == self.config['API_KEY']:
                    print 'logging in invited user'
                    login_user(InvitedAnonymousUser())
                
            #g.search_form = SearchForm()
            g.ns = self.NS
            g.get_summary = get_summary
            g.get_label = get_label
            g.get_entity = self.get_entity
            g.rdflib = rdflib
            g.isinstance = isinstance
            g.db = self.db

        @self.login_manager.user_loader
        def load_user(user_id):
            if user_id != None:
                try:
                    return self.datastore.find_user(id=user_id)
                except:
                    return None
            else:
                return None
            
        extensions = {
            "rdf": "application/rdf+xml",
            "jsonld": "application/ld+json",
            "json": "application/json",
            "ttl": "text/turtle",
            "trig": "application/trig",
            "turtle": "text/turtle",
            "owl": "application/rdf+xml",
            "nq": "application/n-quads",
            "nt": "application/n-triples",
            "html": "text/html"
        }

        dataFormats = {
            "application/rdf+xml" : "xml",
            "application/ld+json" : 'json-ld',
            "text/turtle" : "turtle",
            "application/trig" : "trig",
            "application/n-quads" : "nquads",
            "application/n-triples" : "nt",
            None: "json-ld"
        }

        def get_graphs(graphs):
            query = 'select ?s ?p ?o ?g where {graph ?g {?s ?p ?o} } values ?g { %s }'
            query = query % ' '.join([graph.n3() for graph in graphs])
            #print query
            quads = self.db.store.query(query)
            result = Dataset()
            result.addN(quads)
            return result

        def explain(graph):
            values = ')\n  ('.join([' '.join([x.n3() for x in triple]) for triple in graph.triples((None,None,None))])
            values = 'VALUES (?s ?p ?o)\n{\n('+ values + ')\n}'
            
            try:
                nanopubs = self.db.query('''select distinct ?np where {
    ?np np:hasAssertion?|np:hasProvenance?|np:hasPublicationInfo? ?g;
        np:hasPublicationInfo ?pubinfo;
        np:hasAssertion ?assertion;
    graph ?assertion { ?s ?p ?o.}
}''' + values, initNs={'np':self.NS.np, 'sio':self.NS.sio, 'dc':self.NS.dc, 'foaf':self.NS.foaf})
                result = ConjunctiveGraph()
                for nanopub_uri, in nanopubs:
                    self.nanopub_manager.get(nanopub_uri, result)
            except Exception as e:
                print str(e), entity
                raise e
            return result.resource(entity)
        
        def get_entity_sparql(entity):
            try:
                statements = self.db.query('''select distinct ?s ?p ?o ?g where {
            ?np np:hasAssertion?|np:hasProvenance?|np:hasPublicationInfo? ?g;
                np:hasPublicationInfo ?pubinfo;
                np:hasAssertion ?assertion;

            {graph ?np { ?np sio:isAbout ?e.}}
            UNION
            {graph ?assertion { ?e ?p ?o.}}
            graph ?g { ?s ?p ?o }
        }''',initBindings={'e':entity}, initNs={'np':self.NS.np, 'sio':self.NS.sio, 'dc':self.NS.dc, 'foaf':self.NS.foaf})
                result = ConjunctiveGraph()
                result.addN(statements)
            except Exception as e:
                print str(e), entity
                raise e
            #print result.serialize(format="trig")
            return result.resource(entity)
            
        
        def get_entity_disk(entity):
            try:
                nanopubs = self.db.query('''select distinct ?np where {
            ?np np:hasAssertion?|np:hasProvenance?|np:hasPublicationInfo? ?g;
                np:hasPublicationInfo ?pubinfo;
                np:hasAssertion ?assertion;

            {graph ?np { ?np sio:isAbout ?e.}}
            UNION
            {graph ?assertion { ?e ?p ?o.}}
        }''',initBindings={'e':entity}, initNs={'np':self.NS.np, 'sio':self.NS.sio, 'dc':self.NS.dc, 'foaf':self.NS.foaf})
                result = ConjunctiveGraph()
                for nanopub_uri, in nanopubs:
                    self.nanopub_manager.get(nanopub_uri, result)
#                result.addN(nanopubs)
            except Exception as e:
                print str(e), entity
                raise e
            #print result.serialize(format="trig")
            return result.resource(entity)

        get_entity = get_entity_sparql
        
        self.get_entity = get_entity

        def get_summary(resource):
            summary_properties = [
                self.NS.skos.definition,
                self.NS.dc.abstract,
                self.NS.dc.description,
                self.NS.dc.summary,
                self.NS.RDFS.comment,
                self.NS.dcelements.description
            ]
            return resource.graph.preferredLabel(resource.identifier, default=[], labelProperties=summary_properties)

        self.get_summary = get_summary

        @self.route('/sparql', methods=['GET', 'POST'])
        @login_required
        def sparql_view():
            has_query = False
            for arg in request.args.keys():
                if arg.lower() == "update":
                    return "Update not allowed.", 403
                if arg.lower() == 'query':
                    has_query = True
            if request.method == 'GET' and not has_query:
                return redirect(url_for('sparql_form'))
            #print self.db.store.query_endpoint
            if request.method == 'GET':
                headers = {}
                headers.update(request.headers)
                if 'Content-Length' in headers:
                    del headers['Content-Length']
                req = requests.get(self.db.store.query_endpoint,
                                   headers = headers, params=request.args, stream = True)
            elif request.method == 'POST':
                if 'application/sparql-update' in request.headers['content-type']:
                    return "Update not allowed.", 403
                req = requests.post(self.db.store.query_endpoint, data=request.get_data(),
                                    headers = request.headers, params=request.args, stream = True)
            #print self.db.store.query_endpoint
            #print req.status_code
            response = Response(stream_with_context(req.iter_content()), content_type = req.headers['content-type'])
            #response.headers[con(req.headers)
            return response, req.status_code
        
        @self.route('/sparql.html')
        @login_required
        def sparql_form():
            template_args = dict(ns=self.NS,
                                 g=g,
                                 current_user=current_user,
                                 isinstance=isinstance,
                                 rdflib=rdflib,
                                 hasattr=hasattr,
                                 set=set)

            return render_template('sparql.html',endpoint="/sparql", **template_args)

        
        if 'SATORU_CDN_DIR' in self.config and self.config['SATORU_CDN_DIR'] is not None:
            @self.route('/cdn/<path:filename>')
            def cdn(filename):
                return send_from_directory(self.config['SATORU_CDN_DIR'], werkzeug.utils.secure_filename(filename))
            
        @self.route('/about.<format>')
        @self.route('/about')
        @self.weighted_route('/<path:name>.<format>', compare_key=bottom_compare_key)
        @self.weighted_route('/<path:name>', compare_key=bottom_compare_key)
        @self.route('/')
        @login_required
        def view(name=None, format=None, view=None):
            if format is not None:
                if format in extensions:
                    content_type = extensions[format]
                else:
                    name = '.'.join([name, format])
            if name is not None:
                entity = self.NS.local[name]
            elif 'uri' in request.args:
                entity = URIRef(request.args['uri'])
            else:
                entity = self.NS.local.Home
            resource = self.get_resource(entity)
            
            content_type = request.headers['Accept'] if 'Accept' in request.headers else '*/*'
            #print entity

            htmls = set(['application/xhtml','text/html'])
            if 'view' in request.args or sadi.mimeparse.best_match(htmls, content_type) in htmls:
                return render_view(resource)
            else:
                fmt = dataFormats[sadi.mimeparse.best_match([mt for mt in dataFormats.keys() if mt is not None],content_type)]
                return resource.this().graph.serialize(format=fmt)


        views = {}
        def render_view(resource):
            template_args = dict(ns=self.NS,
                                 this=resource, g=g,
                                 current_user=current_user,
                                 isinstance=isinstance,
                                 get_entity=get_entity,
                                 get_summary=get_summary,
                                 rdflib=rdflib,
                                 hasattr=hasattr,
                                 set=set)
            view = None
            if 'view' in request.args:
                view = request.args['view']
            # 'view' is the default view
            content = resource.value(self.NS.sioc.content)
            #if (view == 'view' or view is None) and content is not None:
            #    if content is not None:
            #        return render_template('content_view.html',content=content, **template_args)
            value = resource.value(self.NS.prov.value)
            if value is not None and view is None:
                headers = {}
                headers['Content-Type'] = 'text/plain'
                content_type = resource.value(self.NS.ov.hasContentType)
                if content_type is not None:
                    headers['Content-Type'] = content_type
                if value.datatype == XSD.base64Binary:
                    return base64.b64decode(value.value), 200, headers
                if value.datatype == XSD.hexBinary:
                    return binascii.unhexlify(value.value), 200, headers
                return value.value, 200, headers

            if view is None:
                view = 'view'

            if 'as' in request.args:
                types = [URIRef(request.args['as']), 0]
            else:
                types = list([(x.identifier, 0) for x in resource[RDF.type]])
            #print types
            #if len(types) == 0:
            types.append([self.NS.RDFS.Resource, 100])
            #print view, resource.identifier, types
            type_string = ' '.join(["(%s %d '%s')" % (x.n3(), i, view) for x, i in types])
            view_query = '''select ?id ?view (count(?mid)+?priority as ?rank) ?class ?c where {
    values (?c ?priority ?id) { %s }
    ?c rdfs:subClassOf* ?mid.
    ?mid rdfs:subClassOf* ?class.
    ?class ?viewProperty ?view.
    ?viewProperty rdfs:subPropertyOf* graphene:hasView.
    ?viewProperty dc:identifier ?id.
} group by ?c ?class order by ?rank
''' % type_string

            #print view_query
            views = list(self.vocab.query(view_query, initNs=dict(graphene=self.NS.graphene, dc=self.NS.dc)))
            #print '\n'.join([str(x.asdict()) for x in views])
            if len(views) == 0:
                abort(404)

            headers = {'Content-Type': "text/html"}
            extension = views[0]['view'].value.split(".")[-1]
            if extension in extensions:
                headers['Content-Type'] = extensions[extension]
                
            # default view (list of nanopubs)
            # if available, replace with class view
            # if available, replace with instance view
            return render_template(views[0]['view'].value, **template_args), 200, headers

        self.api = ld.LinkedDataApi(self, "", self.db.store, "")

        #self.admin = Admin(self, name="graphene", template_mode='bootstrap3')
        #self.admin.add_view(ld.ModelView(self.nanopub_api, default_sort=RDFS.label))
        #self.admin.add_view(ld.ModelView(self.role_api, default_sort=RDFS.label))
        #self.admin.add_view(ld.ModelView(self.user_api, default_sort=foaf.familyName))

        app = self

        self.nanopub_manager = NanopublicationManager(app.db.store, app.config['nanopub_archive_path'],
                                                      Namespace('%s/pub/'%(app.config['lod_prefix'])),
                                                      update_listener=self.nanopub_update_listener)
        class NanopublicationResource(ld.LinkedDataResource):
            decorators = [login_required]

            def __init__(self):
                self.local_resource = app.nanopub_api

            def _can_edit(self, uri):
                if current_user.has_role('Publisher') or current_user.has_role('Editor')  or current_user.has_role('Admin'):
                    return True
                if app.db.query('''ask {
                    ?nanopub np:hasAssertion ?assertion; np:hasPublicationInfo ?info.
                    graph ?info { ?assertion dc:contributor ?user. }
                    }''', initBindings=dict(nanopub=uri, user=current_user.resUri), initNs=dict(np=app.NS.np, dc=app.NS.dc)):
                    #print "Is owner."
                    return True
                return False

            def _get_uri(self, ident):
                return URIRef('%s/pub/%s'%(app.config['lod_prefix'], ident))

            def get(self, ident):
                ident = ident.split("_")[0]
                uri = self._get_uri(ident)
                try:
                    result = app.nanopub_manager.get(uri)
                except IOError:
                    return 'Resource not found', 404
                return result

            def delete(self, ident):
                uri = self._get_uri(ident)
                if not self._can_edit(uri):
                    return '<h1>Not Authorized</h1>', 401
                app.nanopub_manager.retire(uri)
                #self.local_resource.delete(uri)
                return '', 204

            def _get_graph(self):
                inputGraph = ConjunctiveGraph()
                contentType = request.headers['Content-Type']
                sadi.deserialize(inputGraph, request.data, contentType)
                return inputGraph
            
            def put(self, ident):
                nanopub_uri = self._get_uri(ident)
                inputGraph = self._get_graph()
                old_nanopub = self._prep_nanopub(nanopub_uri, inputGraph)
                for nanopub in app.nanopub_manager.prepare(inputGraph):
                    modified = Literal(datetime.utcnow())
                    nanopub.pubinfo.add((nanopub.assertion.identifier, app.NS.prov.wasRevisionOf, old_nanopub.assertion.identifier))
                    nanopub.pubinfo.add((old_nanopub.assertion.identifier, app.NS.prov.invalidatedAtTime, modified))
                    nanopub.pubinfo.add((nanopub.assertion.identifier, app.NS.dc.modified, modified))
                    app.nanopub_manager.retire(nanopub_uri)
                    app.nanopub_manager.publish(nanopub)

            def _prep_nanopub(self, nanopub_uri, graph):
                nanopub = Nanopublication(store=graph.store, identifier=nanopub_uri)
                about = nanopub.nanopub_resource.value(app.NS.sio.isAbout)
                #print nanopub.assertion_resource.identifier, about
                self._prep_graph(nanopub.assertion_resource, about.identifier)
                self._prep_graph(nanopub.pubinfo_resource, nanopub.assertion_resource.identifier)
                self._prep_graph(nanopub.provenance_resource, nanopub.assertion_resource.identifier)
                nanopub.pubinfo.add((nanopub.assertion.identifier, app.NS.dc.contributor, current_user.resUri))
                return nanopub
            
            def post(self, ident=None):
                if ident is not None:
                    return self.put(ident)
                inputGraph = self._get_graph()
                for nanopub_uri in inputGraph.subjects(rdflib.RDF.type, app.NS.np.Nanopublication):
                    nanopub = self._prep_nanopub(nanopub_uri, inputGraph)
                    nanopub.pubinfo.add((nanopub.assertion.identifier, app.NS.dc.created, Literal(datetime.utcnow())))
                for nanopub in app.nanopub_manager.prepare(inputGraph):
                    app.nanopub_manager.publish(nanopub)

                return '', 201


            def _prep_graph(self, resource, about = None):
                #print '_prep_graph', resource.identifier, about
                content_type = resource.value(app.NS.ov.hasContentType)
                if content_type is not None:
                    content_type = content_type.value
                #print 'graph content type', resource.identifier, content_type
                #print resource.graph.serialize(format="nquads")
                g = Graph(store=resource.graph.store,identifier=resource.identifier)
                text = resource.value(app.NS.prov.value)
                if content_type is not None and text is not None:
                    #print 'Content type:', content_type, resource.identifier
                    html = None
                    if content_type in ["text/html", "application/xhtml+xml"]:
                        html = Literal(text.value, datatype=RDF.HTML)
                    if content_type == 'text/markdown':
                        #print "Aha, markdown!"
                        #print text.value
                        html = markdown.markdown(text.value, extensions=['rdfa'])
                        attributes = ['vocab="%s"' % app.NS.local,
                                      'base="%s"'% app.NS.local,
                                      'prefix="%s"' % ' '.join(['%s: %s'% x for x in app.NS.prefixes.items()])]
                        if about is not None:
                            attributes.append('resource="%s"' % about)
                        html = '<div %s>%s</div>' % (' '.join(attributes), html)
                        html = Literal(html, datatype=RDF.HTML)
                        text = html
                        content_type = "text/html"
                    #print resource.identifier, content_type
                    if html is not None:
                        resource.add(app.NS.sioc.content, html)
                        try:
                            g.parse(data=text, format='rdfa')
                        except:
                            pass
                    else:
                        #print "Deserializing", g.identifier, 'as', content_type
                        #print dataFormats
                        if content_type in dataFormats:
                            g.parse(data=text, format=dataFormats[content_type])
                            #print len(g)
                        else:
                            print "not attempting to deserialize."
#                            try:
#                                sadi.deserialize(g, text, content_type)
#                            except:
#                                pass
                #print Graph(store=resource.graph.store).serialize(format="trig")
        self.api.add_resource(NanopublicationResource, '/pub', '/pub/<ident>')


def config_str_to_obj(cfg):
    if isinstance(cfg, basestring):
        module = __import__('config', fromlist=[cfg])
        return getattr(module, cfg)
    return cfg


def app_factory(config, app_name, blueprints=None):
    # you can use Empty directly if you wish
    app = App(app_name)
    config = config_str_to_obj(config)
    #print dir(config)
    app.configure(config)
    if blueprints:
        app.add_blueprint_list(blueprints)
    app.setup()

    return app


def heroku():
    from config import Config, project_name
    # setup app through APP_CONFIG envvar
    return app_factory(Config, project_name)

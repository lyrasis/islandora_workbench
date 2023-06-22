from ruamel.yaml import YAML
import mimetypes
import requests
import re
import logging
import xml.etree.ElementTree as ET
from xml.etree.ElementTree import ElementTree
import sys
import os
from rich.console import Console
from rich.table import Table


class i7ImportUtilities:

    def __init__(self, config_location):
        self.config_location = config_location
        self.config = self.get_config()
        self.validate()
        logging.basicConfig(
            filename=self.config['log_file_path'],
            level=logging.INFO,
            filemode='a',
            format='%(asctime)s - %(levelname)s - %(message)s',
            datefmt='%d-%b-%y %H:%M:%S')

    default_config = {
        'solr_base_url': 'http://localhost:8080/solr',
        'islandora_base_url': 'http://localhost:8000',
        'csv_output_path': 'islandora7_metadata.csv',
        'obj_directory': '/tmp/objs',
        'failure_report': 'failure_report.txt',
        'log_file_path': 'islandora_content.log',
        'fetch_files': True,
        'get_file_url': False,
        'namespace': '*',
        'standard_fields': ['PID', 'RELS_EXT_hasModel_uri_s', 'RELS_EXT_isMemberOfCollection_uri_ms',
                            'RELS_EXT_isMemberOf_uri_ms', 'RELS_EXT_isConstituentOf_uri_ms',
                            'RELS_EXT_isPageOf_uri_ms'],
        'field_pattern': 'mods_.*(_s|_ms)$',
        'field_pattern_do_not_want': '(marcrelator|isSequenceNumberOf)',
        'id_field': 'PID',
        'id_start_number': 1,
        'datastreams': ['OBJ', 'PDF'],
        'debug': False,
        'deep_debug': False,
        'collection': False,
        'content_model': False,
        'solr_filters': False
    }

    def get_config(self):
        yaml = YAML()
        config = self.default_config
        with open(self.config_location, 'r') as stream:
            try:
                loaded = yaml.load(stream)
            except OSError:
                print('Failed')
        for key, value in loaded.items():
            config[key] = value
        if 'get_file_url' in loaded.keys() and 'fetch_files' not in loaded.keys():
            config['fetch_files'] = False
        if config['deep_debug']:
            config['debug'] = True
        return config

    def get_metadata_solr_request(self, location):
        with open(location, 'r') as file:
            solr_metadata_request = file.read()
        return solr_metadata_request

    def get_extension_from_mimetype(self, mimetype):
        # mimetypes.add_type() is not working, e.g. mimetypes.add_type('image/jpeg', '.jpg')
        # Maybe related to https://bugs.python.org/issue4963? In the meantime, provide our own
        # MIMETYPE to extension mapping for common types, then let mimetypes guess at others.
        map = {'image/jpeg': '.jpg',
               'image/jp2': '.jp2',
               'image/png': '.png',
               'application/xml': '.xml',
               'text/xml': '.xml',
                # weirdly, this is what Islandora provides for XML docs
               'text/xml;charset=UTF-8': '.xml'
               }
        if mimetype in map:
            return map[mimetype]
        else:
            return mimetypes.guess_extension(mimetype)

    def get_percentage(self, part, whole):
        return 100 * float(part) / float(whole)

    def parse_rels_ext(self, pid):
        rels_ext_url = f"{self.config['islandora_base_url']}/islandora/object/{pid}/datastream/RELS-EXT/download"
        if self.config['deep_debug']:
            print(f"\n{rels_ext_url}")
        try:
            rels_ext_download_response = requests.get(url=rels_ext_url, allow_redirects=True)
            if rels_ext_download_response.ok:
                rel_ext = {}
                rels_ext_xml = rels_ext_download_response.content.decode()
                if self.config['deep_debug']:
                    print(rels_ext_xml)
                root = ET.fromstring(rels_ext_xml)
                description = root.find('.//{http://www.w3.org/1999/02/22-rdf-syntax-ns#}Description')
                for x in description:
                    tag = x.tag[x.tag.find('}') + 1:]
                    text = x.text
                    if x.attrib.items():
                        text = next(iter(x.attrib.items()))[1]
                        text = text[text.find('/') + 1:]
                    rel_ext[tag] = text
                return rel_ext
            else:
                message = f"\nBad response from server for item {pid} : {rels_ext_download_response.status_code}"
                logging.error(f"\nBad response from server for item {pid} : {rels_ext_download_response.status_code}")
                if self.config['debug']:
                    print(message)
        except requests.exceptions.RequestException as e:
            raise SystemExit(e)

    def get_default_metadata_solr_request(self):
        # This query gets all fields in the index. Does not need to be user-configurable.
        fields_solr_url = f"{self.config['solr_base_url']}/select?q=*:*&wt=csv&rows=0&fl=*"

        # Get the complete field list from Solr and filter it. The filtered field list is
        # then used in another query to get the populated CSV data.
        try:
            field_list_response = requests.get(url=fields_solr_url, allow_redirects=True)
            raw_field_list = field_list_response.content.decode()
        except requests.exceptions.RequestException as e:
            raise SystemExit(e)

        field_list = raw_field_list.split(',')
        filtered_field_list = [keep for keep in field_list if re.search(self.config['field_pattern'], keep)]
        filtered_field_list = [discard for discard in filtered_field_list if
                               not re.search(self.config['field_pattern_do_not_want'], discard)]

        # Add required fieldnames.
        self.config['standard_fields'].reverse()
        for standard_field in self.config['standard_fields']:
            filtered_field_list.insert(0, standard_field)
        fields_param = ','.join(filtered_field_list)
        query = f"{self.config['solr_base_url']}/select?q=PID:{self.config['namespace']}*&wt=csv&rows=1000000&fl={fields_param}"
        if self.config['collection']:
            collection = self.config['collection']
            query = f'{query}&fq=RELS_EXT_isMemberOfCollection_uri_s:"info\:fedora/{collection}"'
        if self.config['content_model']:
            model = self.config['content_model']
            # had to change hasModel_uri_s to hasModel_uri_ms
            query = f'{query}&fq=RELS_EXT_hasModel_uri_ms:"info\:fedora/{model}"'
        if self.config['solr_filters']:
            for filter in self.config['solr_filters']:
                for key, value in filter.items():
                    query = f'{query}&fq={key}:"{value}"'

        # Get the populated CSV from Solr, with the object namespace and field list filters applied.
        return query

    # Returns DC/XML data reformatted in a structure that DSpace's SAF expects
    def create_dspace_dc_xml(self, row):

        dublin_core = ET.Element('dublin_core')

        for field in row.keys():
            if re.search("^dc\.", field):
                if not row[field] == '':
                    # chopping off dc. for easier processing
                    clean_field = field.replace('dc.', '')
                    # cleaning up Solr escapes that aren't parsed by CSV
                    # have to get create with how to clean this
                    # because there are multivalued fields that
                    # have non-delimiter commas
                    value = row[field].replace('\\,', '%COMMA%').replace('\\', '')
                    # handling multivalued fields
                    if clean_field in ['contributor', 'coverage', 'identifier', 'subject', 'type']:
                        for separate_value in value.split(','):
                            dcvalue = ET.Element('dcvalue')
                            separate_value = separate_value.replace('%COMMA%', ',')
                            dcvalue.text = separate_value
                            dcvalue.set('element', clean_field)
                            dcvalue.set('qualifier', 'none')
                            dublin_core.append(dcvalue)
                    else:
                        dcvalue = ET.Element('dcvalue')
                        value = value.replace('%COMMA%', ',')
                        dcvalue.text = value
                        dcvalue.set('element', clean_field)
                        if re.search("^language", clean_field):
                            dcvalue.set('qualifier', 'iso')
                        else:
                            dcvalue.set('qualifier', 'none')
                        dublin_core.append(dcvalue)
                
        return dublin_core

        

    # Validates config.
    def validate(self):
        error_messages = []
        if self.config['get_file_url'] and self.config['fetch_files']:
            message = f"'get_file_url' and 'fetch_files' cannot both be selected."
            error_messages.append(message)
        if error_messages:
            sys.exit('Error: ' + message)

    # Gets file from i7 installation
    def get_i7_asset(self, pid, datastream):
        try:
            obj_url = f"{self.config['islandora_base_url']}/islandora/object/{pid}/datastream/{datastream}/download"
            if self.config['get_file_url']:
                obj_download_response = requests.head(url=obj_url, allow_redirects=True)
            else:
                obj_download_response = requests.get(url=obj_url, allow_redirects=True)
            if obj_download_response.status_code == 200:
                # Get MIMETYPE from 'Content-Type' header
                obj_mimetype = obj_download_response.headers['content-type']
                obj_extension = self.get_extension_from_mimetype(obj_mimetype)
                if self.config['fetch_files'] and obj_extension:
                    obj_filename = pid.replace(':', '_')
                    obj_basename = obj_filename + obj_extension
                    # Save to file with name based on PID and extension based on MIMETYPE
                    obj_file_path = os.path.join(self.config['obj_directory'], obj_basename)
                    open(obj_file_path, 'wb+').write(obj_download_response.content)
                    return obj_basename

                if self.config['get_file_url'] and obj_extension:
                    return obj_url
                if obj_download_response.status_code == 404:
                    logging.warning(f"{obj_url} not found.")
                    return None

        except requests.exceptions.RequestException as e:
            logging.info(e)
            return None

    # Gets file from i7 installation
    def get_i7_asset_saf_output(self, pid, datastream, parent_collection_pid, row):
        try:
            obj_url = f"{self.config['islandora_base_url']}/islandora/object/{pid}/datastream/{datastream}/download"
            if self.config['get_file_url']:
                obj_download_response = requests.head(url=obj_url, allow_redirects=True)
            else:
                obj_download_response = requests.get(url=obj_url, allow_redirects=True)
            if obj_download_response.status_code == 200:
                # Get MIMETYPE from 'Content-Type' header
                obj_mimetype = obj_download_response.headers['content-type']
                obj_extension = self.get_extension_from_mimetype(obj_mimetype)
                if self.config['fetch_files'] and obj_extension:
                    obj_filename = pid.replace(':', '_')
                    obj_basename = obj_filename + obj_extension
                    obj_directory = os.path.join(self.config['obj_directory'], obj_filename)
                    if not os.path.exists(obj_directory):
                        os.makedirs(obj_directory)
                    # Save to file with name based on PID and extension based on MIMETYPE
                    obj_file_path = os.path.join(obj_directory, obj_basename)
                    open(obj_file_path, 'wb+').write(obj_download_response.content)
                    # Create contents file
                    open(f"{obj_directory}/contents", 'w').write(obj_basename)
                    # Create collections file
                    open(f"{obj_directory}/collections", 'w').write(parent_collection_pid)
                    # Create DC/XML in SAF format
                    dc_xml = self.create_dspace_dc_xml(row)
                    with open(f"{obj_directory}/dublin_core.xml", 'wb') as f:
                        ElementTree(dc_xml).write(f, encoding='utf-8')
                    # open(f"{obj_directory}/dublin_core.xml", 'w').write(parent_collection_pid, encoding="utf-8")                    

                    return obj_basename

                if self.config['get_file_url'] and obj_extension:
                    return obj_url
                if obj_download_response.status_code == 404:
                    logging.warning(f"{obj_url} not found.")
                    return None

        except requests.exceptions.RequestException as e:
            logging.info(e)
            return None

    # Convenience function for debugging - Prints config to console screen.
    def print_config(self):
        table = Table(title="i7 Import Script Configuration")
        table.add_column("Parameter", justify="left")
        table.add_column("Value", justify="left")
        for key, value in self.config.items():
            table.add_row(key, str(value))
        console = Console()
        console.print(table)

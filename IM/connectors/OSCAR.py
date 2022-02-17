# IM - Infrastructure Manager
# Copyright (C) 2011 - GRyCAP - Universitat Politecnica de Valencia
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import base64
import json
import requests
from IM.VirtualMachine import VirtualMachine
from .CloudConnector import CloudConnector
from radl.radl import Feature
try:
    from urlparse import urlparse
except ImportError:
    from urllib.parse import urlparse

class OSCARCloudConnector(CloudConnector):
    """
    Cloud Launcher to create OSCAR functions.
    """

    type = "OSCAR"
    """str with the name of the provider."""

    def concrete_system(self, radl_system, str_url, auth_data):
        url = urlparse(str_url)
        protocol = url[0]
        src_host = url[1].split(':')[0]

        if protocol == "oscar" and self.cloud.server == src_host:
            res_system = radl_system.clone()
            return res_system
        else:
            return None

    def _get_auth_header(self, auth_data):
        auths = auth_data.getAuthInfo(self.type, self.cloud.server)
        if not auths:
            raise Exception("No auth data has been specified to OSCAR.")
        else:
            auth = auths[0]
    
        if 'username' in auth and 'password' in auth:
            return "Basic %s" % base64.b64encode("%s:%s" % (auth['username'], auth['password']))
        elif 'token' in auth:
            return "Bearer %s" % auth['token']
        else:
            self.log_error("No correct auth data has been specified to OSCAR: username and password or token")
            raise Exception("No correct auth data has been specified to OSCAR: username and password or token")

    def _get_oscar_url(self):
        protocol = self.cloud.protocol
        if not protocol:
            protocol = "http"
        port = self.cloud.port
        if port == -1:
            if protocol == "http":
                port = 80
            elif protocol == "https":
                port = 443
            else:
                raise Exception("Invalid port/protocol specified for OpenStack site: %s" % self.cloud.server)

        return protocol + "://" + self.cloud.server + ":" + str(port) + self.cloud.path

    def _get_service_json(self, radl_system):
        service = {
            "name": radl_system.getValue("name"),
            "memory": "%dMi" % radl_system.getFeature('memory.size').getValue('M'),
            "cpu": radl_system.getValue("cpu.count"),
            "script": radl_system.getValue("script"),
        }

        url_image = urlparse(radl_system.getValue("disk.0.image.url"))
        service["image"] = url_image.path[1:]

        vars = {}
        for elem in  radl_system.getValue("environment.variables"):
            parts = elem.split(":")
            vars[parts[0]] = parts[1]
        if vars:
            service["environment"] = {"Variables": vars}

        for elem in ["input", "output"]:
            service[elem] = {
                "storage_provider": radl_system.getValue("%s.provider" % elem),
                "path": radl_system.getValue("%s.path" % elem)
            }
            if radl_system.getValue("%s.suffix" % elem):
                service[elem]["suffix"] = radl_system.getValue("%s.suffix" % elem)
            if radl_system.getValue("%s.prefix" % elem):
                service[elem]["prefix"] = radl_system.getValue("%s.prefix" % elem)

        storage_providers = {}
        i = 0
        while radl_system.getValue("minio." + str(i) + ".id"):
            sid = radl_system.getValue("minio." + str(i) + ".id")
            endpoint = radl_system.getValue("minio." + str(i) + ".endpoint")
            region = radl_system.getValue("minio." + str(i) + ".region")
            secret_key = radl_system.getValue("minio." + str(i) + ".secret_key")
            access_key = radl_system.getValue("minio." + str(i) + ".access_key")
            if "minio" not in storage_providers:
                storage_providers["minio"] = {}
            storage_providers["minio"][sid] = {
                "access_key": access_key,
                "secret_key": secret_key,
                "endpoint": endpoint,
                "region": region,
                "verify": False
            }
            i += 1

        i = 0
        while radl_system.getValue("s3." + str(i) + ".id"):
            sid = radl_system.getValue("s3." + str(i) + ".id")
            region = radl_system.getValue("s3." + str(i) + ".region")
            secret_key = radl_system.getValue("s3." + str(i) + ".secret_key")
            access_key = radl_system.getValue("s3." + str(i) + ".access_key")
            if "s3" not in storage_providers:
                storage_providers["s3"] = {}
            storage_providers["s3"][sid] = {
                "access_key": access_key,
                "secret_key": secret_key,
                "region": region
            }
            i += 1

        i = 0
        while radl_system.getValue("onedata." + str(i) + ".id"):
            sid = radl_system.getValue("onedata." + str(i) + ".id")
            oneprovider = radl_system.getValue("onedata." + str(i) + ".oneprovider")
            token = radl_system.getValue("onedata." + str(i) + ".token")
            space = radl_system.getValue("onedata." + str(i) + ".space")
            if "s3" not in storage_providers:
                storage_providers["s3"] = {}
            storage_providers["s3"][sid] = {
                "oneprovider": oneprovider,
                "token": token,
                "space": space
            }
            i += 1

        if storage_providers:
            service["storage_providers"] = storage_providers

        return service

    def launch(self, inf, radl, requested_radl, num_vm, auth_data):
        res = []
        for func_num in range(num_vm):
            vm_id = radl.systems[0].getValue("name") 
            if func_num > 0:
                vm_id += "%d" % func_num
            vm = VirtualMachine(inf, vm_id, self.cloud,
                                requested_radl, requested_radl)
            vm.destroy = True
            vm.info.systems[0].setValue('provider.type', self.type)
            vm.info.systems[0].setValue('instance_id', str(vm_id))
            inf.add_vm(vm)

            try:
                url = "%s/system/services" % self._get_oscar_url()
                service = self._get_service_json(radl.systems[0])
                headers = {"Authorization": self._get_auth_header(auth_data)}
                response = requests.request("POST", url, data=json.dumps(service),
                                            headers=headers, verify=self.verify_ssl)
                if response.status_code == 201:
                    vm.destroy = False
                    res.append((True, vm))
                else:
                    msg = "Error code %d: %s" % (response.status_code, response.text)
                    res.append((False, msg))
            except Exception as ex:
                self.log_exception("Error creating OSCAR function: %s." % ex)
                res.append((False, "%s" % ex))

            res.append((True, vm))

        return res

    def finalize(self, vm, last, auth_data):
        try:
            url = "%s/system/services/%s" % (self._get_oscar_url(), vm.id)
            headers = {"Authorization": self._get_auth_header(auth_data)}
            response = requests.request("DELETE", url, headers=headers, verify=self.verify_ssl)
            if response.status_code != 204:
                msg = "Error code %d: %s" % (response.status_code, response.text)
                return False, msg
        except Exception as ex:
            self.log_exception("Error deletting OSCAR function: %s." % ex)
            return False, "%s" % ex

        return True, ""

    def update_system_info_from_service_info(self, system, service_info):
        system.addFeature(Feature("cpu.count", "=", service_info["cpu"]),
                                  conflict="other", missing="other")
    
    def updateVMInfo(self, vm, auth_data):
        try:
            url = "%s/system/services/%s" % (self._get_oscar_url(), vm.id)
            headers = {"Authorization": self._get_auth_header(auth_data)}
            response = requests.request("GET", url, headers=headers, verify=self.verify_ssl)
            if response.status_code != 200:
                msg = "Error code %d: %s" % (response.status_code, response.text)
                return False, msg
            else:
                self.update_system_info_from_service_info(vm.info.systems[0], response.json())
                vm.state = VirtualMachine.RUNNING
                return True, vm
        except Exception as ex:
            self.log_exception("Error getting OSCAR function: %s." % ex)
            return False, "%s" % ex

    def alterVM(self, vm, radl, auth_data):
        try:
            service = self._get_service_json(radl.systems[0])
            url = "%s/system/services/%s" % (self._get_oscar_url(), vm.id)
            headers = {"Authorization": self._get_auth_header(auth_data)}
            response = requests.request("PUT", url, data=json.dumps(service), headers=headers, verify=self.verify_ssl)
            if response.status_code != 204:
                msg = "Error code %d: %s" % (response.status_code, response.text)
                return False, msg
            else:
                return True, ""
        except Exception as ex:
            self.log_exception("Error getting OSCAR function: %s." % ex)
            return False, "%s" % ex

